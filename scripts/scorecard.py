"""Scorecard core: parse results, map each into one category, score, render.

Pure parsing, scoring and rendering (no subprocess calls). The one rule behind all of
it: a category that did not run is never green. Skipped means not run, zero results
means not run, and a required row that did not run fails the verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorecard_config import (
    CATEGORIES,
    FILE_MAP,
    FORK_PR_OPTIONAL,
    NAME_OVERRIDES,
    PROMPTFOO_SOURCES,
    RESIDUAL_DESCRIPTION_PREFIX,
)

Outcome = Literal["passed", "failed", "skipped", "error"]
FALLBACK_KEY = "other_deterministic"
PARTIAL_FORK = "PASS (partial: model suites not run on fork PR)"


@dataclass
class TestResult:
    __test__ = False  # not a pytest class

    file: str
    name: str
    outcome: Outcome


@dataclass
class CaseResult:
    key: str
    attempts: int
    passes: int
    errors: int  # provider/runtime errors: the attempt did not complete
    failures: int = 0  # assertion failures: the attempt ran and the output failed

    @property
    def passed(self) -> bool:
        return (
            self.attempts > 0
            and self.passes == self.attempts
            and self.errors == 0
            and self.failures == 0
        )


@dataclass
class Row:
    key: str
    title: str
    kind: str
    threshold: str
    required: bool
    ran: bool
    runs: int
    passed: int
    total: int
    verdict: str
    failures: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    attempts: int = 0
    changed: list[str] = field(default_factory=list)


@dataclass
class Scorecard:
    verdict: str
    event: str
    rows: list[Row]
    meta: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- parsers

def _junit_file(tc: ET.Element) -> str:
    f = tc.get("file")  # the orchestrator runs pytest with junit_family=xunit1, so this is set
    if f:
        return f.replace("\\", "/")
    # Fallback: classname is the dotted module path, possibly ending in a class name.
    parts = [p for p in (tc.get("classname") or "").split(".") if p]
    while parts and parts[-1][:1].isupper():
        parts.pop()
    return "/".join(parts) + ".py" if parts else ""


def parse_junit(path) -> list[TestResult]:
    out: list[TestResult] = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        outcome: Outcome = "passed"
        for child in tc:
            if child.tag == "skipped":
                outcome = "skipped"
            elif child.tag == "failure":
                outcome = "failed"
            elif child.tag == "error":
                outcome = "error"
            if outcome != "passed":
                break
        out.append(TestResult(_junit_file(tc), tc.get("name") or "", outcome))
    return out


_VITEST = {"passed": "passed", "failed": "failed"}


def parse_vitest_json(path) -> list[TestResult]:
    data = json.loads(Path(path).read_text())
    out: list[TestResult] = []
    for tr in data.get("testResults", []):
        fname = tr.get("name", "").replace("\\", "/")
        before = len(out)
        for a in tr.get("assertionResults", []):
            outcome: Outcome = _VITEST.get(a.get("status"), "skipped")  # type: ignore[assignment]
            out.append(TestResult(fname, a.get("fullName") or a.get("title") or "", outcome))
        # A suite that fails to import has status "failed" and no assertions: that is
        # a failure, not an absence.
        if tr.get("status") == "failed" and not any(
            r.outcome in ("failed", "error") for r in out[before:]
        ):
            out.append(TestResult(fname, "<suite failed to run>", "error"))
    broken = (
        data.get("success") is False
        or data.get("numFailedTestSuites", 0)
        or data.get("numRuntimeErrorTestSuites", 0)
    )
    if broken and not any(r.outcome in ("failed", "error") for r in out):
        out.append(TestResult("vitest", "<run reported failure>", "error"))
    return out


def _load_cases(path) -> list[tuple[str, CaseResult]]:
    """Group raw promptfoo results by description + vars. Returns (description, case)."""
    data = json.loads(Path(path).read_text())
    grouped: dict[str, list] = {}
    for item in data["results"]["results"]:
        desc = (item.get("testCase") or {}).get("description") or ""
        vars_ = item.get("vars")
        if vars_ is None:
            vars_ = (item.get("testCase") or {}).get("vars") or {}
        key = desc + "|" + json.dumps(vars_, sort_keys=True)
        g = grouped.setdefault(key, [desc, 0, 0, 0, 0])
        g[1] += 1
        reason = item.get("failureReason")
        if reason == 1:
            g[4] += 1  # assertion failed: the attempt ran, the output was bad
        elif reason == 2 or item.get("error"):
            # provider/runtime error; also any error with no usable failureReason
            # (older or missing field), which fails closed as an error
            g[3] += 1
        elif item.get("success") is True:
            g[2] += 1
        else:
            g[4] += 1  # not a pass, no error message: count as a failed attempt
    return [(g[0], CaseResult(k, g[1], g[2], g[3], g[4])) for k, g in grouped.items()]


def parse_promptfoo(path, category) -> list[CaseResult]:
    """Cases for `category`. Cases whose description starts with the KNOWN RESIDUAL
    prefix belong to known_residuals; everything else to the file's own category."""
    want_residual = category == "known_residuals"
    return [
        c
        for desc, c in _load_cases(path)
        if desc.startswith(RESIDUAL_DESCRIPTION_PREFIX) == want_residual
    ]


# ------------------------------------------------------------ categorizing

def _match(f: str, p: str) -> bool:
    return f == p or p.endswith("/" + f) or f.endswith("/" + p)


def _resolve_path(file: str, paths) -> str | None:
    """The FILE_MAP-style path a result's file refers to (suffix match, most specific)."""
    f = file.replace("\\", "/")
    hits = [p for p in paths if _match(f, p)]
    return max(hits, key=len) if hits else None


def categorize(results, file_map=FILE_MAP, overrides=NAME_OVERRIDES) -> dict[str, list[TestResult]]:
    """Every result lands in exactly one bucket. A (file, prefix) override wins, then
    the file map, then other_deterministic."""
    buckets: dict[str, list[TestResult]] = {}
    for r in results:
        key = next(
            (k for f, prefix, k in overrides if _match(r.file.replace("\\", "/"), f)
             and r.name.startswith(prefix)),
            None,
        )
        if key is None:
            path = _resolve_path(r.file, file_map)
            key = file_map[path] if path else FALLBACK_KEY
        buckets.setdefault(key, []).append(r)
    return buckets


# ----------------------------------------------------------------- scoring

def _row(cat, items, ran_flag, event, repeat, expected, paths, missing, pins=False) -> Row:
    tests = [i for i in items if isinstance(i, TestResult)]
    cases = [i for i in items if isinstance(i, CaseResult)]
    if cases and repeat is None:
        raise ValueError("score() needs the expected repeat N when promptfoo cases are present")
    watch = cat.kind == "watch"
    has_half = cat.key in FORK_PR_OPTIONAL  # has a Promptfoo half
    model_required = has_half and event != "fork_pr"
    executed = [t for t in tests if t.outcome != "skipped"]
    hard: list[str] = []
    changed: list[str] = []

    # Deterministic half: required on every event, scored independently of Promptfoo.
    for t in tests:
        label = f"{t.file}::{t.name} ({t.outcome})"
        if t.outcome == "passed":
            continue
        if watch and t.outcome == "failed":
            changed.append(label)  # a completed test whose pinned behavior moved: report, do not gate
        else:
            hard.append(label)  # failed, errored or skipped; error and skipped mean not run
    hard += [f"MISSING: {m} contributed no executed result" for m in missing]
    det_expected = (not has_half) or bool(paths) or bool(tests) or pins
    det_ran = bool(ran_flag) and len(executed) > 0
    if det_expected and not det_ran:
        hard.append("NOT RUN: no executed deterministic result")

    # Promptfoo half.
    for c in cases:
        why = None
        if c.attempts == 0:
            why = "no attempts"
        elif c.errors > 0:
            why = f"{c.errors} errored attempt(s)"
        elif c.attempts != repeat:
            why = f"{c.attempts} attempts, expected {repeat}"
        if why:
            hard.append(f"{c.key} ({why})")
        elif c.passes < c.attempts:
            msg = f"{c.key} ({c.passes}/{c.attempts} passes, {c.failures} failed assertion(s))"
            (changed if watch else hard).append(msg)
    n_exp = (expected or {}).get(cat.key)
    if n_exp is not None and cases and len(cases) != n_exp:
        hard.append(f"PARTIAL RUN: {len(cases)} cases, expected {n_exp}")
    model_ran = bool(ran_flag) and any(c.attempts - c.errors > 0 for c in cases)
    if model_required and not model_ran:
        hard.append("NOT RUN: no completed Promptfoo attempts")

    ran = (det_ran or not det_expected) and (model_ran if has_half else True)
    attempts = sum(c.attempts for c in cases)
    passed = sum(t.outcome == "passed" for t in tests) + sum(
        c.attempts == repeat and c.passes == c.attempts and c.errors == 0 for c in cases
    )
    total = len(tests) + len(cases)
    runs = repeat if cases else len(executed)
    if hard:
        verdict = "FAIL"
    elif changed:
        verdict = "CHANGED" if ran else "CHANGED (partial)"  # partial: fork PR, model half not run
    elif not ran:
        verdict = "NOT RUN"  # only reachable for the fork-PR model half
    else:
        verdict = "PASS"
    sources = sorted(paths)
    if cat.key in PROMPTFOO_SOURCES:
        sources.append(PROMPTFOO_SOURCES[cat.key])
    return Row(cat.key, cat.title, cat.kind, cat.threshold,
               cat.required and not (event == "fork_pr" and has_half),
               ran, runs, passed, total, verdict, hard, sources, attempts, changed)


def score(categories, buckets, ran_flags, event, meta=None, repeat=None,
          expected_cases=None, file_map=FILE_MAP, overrides=NAME_OVERRIDES) -> Scorecard:
    """`repeat` is the N every Promptfoo case must have run. `expected_cases` maps a
    category key to how many cases its run should contain. Every file in `file_map`
    must contribute at least one executed result, or its row FAILs."""
    ran_flags = ran_flags or {}
    paths_by_key: dict[str, list[str]] = {}
    for path, key in file_map.items():
        paths_by_key.setdefault(key, []).append(path)
    seen = {
        _resolve_path(t.file, file_map)
        for items in buckets.values() for t in items
        if isinstance(t, TestResult) and t.outcome != "skipped"
    }
    executed_tests = [
        t for items in buckets.values() for t in items
        if isinstance(t, TestResult) and t.outcome != "skipped"
    ]
    pin_missing: dict[str, list[str]] = {}
    for f, prefix, key in overrides:
        if not any(_match(t.file.replace("\\", "/"), f) and t.name.startswith(prefix)
                   for t in executed_tests):
            pin_missing.setdefault(key, []).append(f"({f}, {prefix}*)")
    pinned = {k for _, _, k in overrides}
    rows = []
    for cat in categories:
        paths = paths_by_key.get(cat.key, [])
        missing = [p for p in paths if p not in seen] + pin_missing.get(cat.key, [])
        rows.append(_row(cat, buckets.get(cat.key, []), ran_flags.get(cat.key, True),
                         event, repeat, expected_cases, paths, missing, cat.key in pinned))
    if any(r.verdict == "FAIL" for r in rows):
        verdict = "FAIL"
    elif event == "fork_pr" and any(r.key in FORK_PR_OPTIONAL and not r.ran for r in rows):
        verdict = PARTIAL_FORK
    else:
        verdict = "PASS"
    return Scorecard(verdict, event, rows, dict(meta or {}))


# ------------------------------------------------- expected case counts

def _load_tests(entries, base: Path) -> list[dict]:
    cases: list[dict] = []
    for e in entries or []:
        if isinstance(e, str) and e.startswith("file://"):
            import yaml

            loaded = yaml.safe_load((base / e[len("file://"):]).read_text())
            cases += _load_tests(loaded if isinstance(loaded, list) else [loaded], base)
        elif isinstance(e, dict):
            cases.append(e)
    return cases


def config_case_counts(config_path) -> tuple[int, int]:
    """(non-residual cases, KNOWN RESIDUAL cases) in a Promptfoo config's tests:, following
    file:// references relative to the config's directory. Raises ValueError if the
    config cannot be loaded."""
    import yaml

    path = Path(config_path)
    try:
        cfg = yaml.safe_load(path.read_text())
        cases = _load_tests(cfg.get("tests"), path.parent)
    except (OSError, AttributeError, yaml.YAMLError) as e:
        raise ValueError(f"cannot load promptfoo config {path}: {e}") from e
    resid = sum(str(c.get("description", "")).startswith(RESIDUAL_DESCRIPTION_PREFIX) for c in cases)
    return len(cases) - resid, resid


# --------------------------------------------------------------- rendering

def render_json(sc: Scorecard) -> dict:
    return {
        "verdict": sc.verdict,
        "event": sc.event,
        "meta": sc.meta,
        "rows": [
            {
                "key": r.key, "title": r.title, "kind": r.kind, "required": r.required,
                "ran": r.ran, "runs": r.runs, "attempts": r.attempts, "passed": r.passed,
                "total": r.total, "threshold": r.threshold, "verdict": r.verdict,
                "failures": r.failures, "changed": r.changed, "sources": r.sources,
            }
            for r in sc.rows
        ],
    }


def render_md(sc: Scorecard) -> str:
    lines = ["# Security scorecard", "", f"**Verdict: {sc.verdict}**", ""]
    lines.append(f"- event: {sc.event}")
    lines += [f"- {k}: {v}" for k, v in sc.meta.items()]
    lines += [
        "",
        "| Category | Kind | Ran | Runs | Passed/Total | Threshold | Verdict | Sources |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sc.rows:
        src = ", ".join(x.rsplit("/", 1)[-1] for x in r.sources) or "none"
        lines.append(
            f"| {r.title} | {r.kind} | {'yes' if r.ran else 'NO'} | {r.runs} | "
            f"{r.passed}/{r.total} | {r.threshold} | {r.verdict} | {src} |"
        )
    detail = [r for r in sc.rows if r.failures or r.changed or not r.ran]
    if detail:
        lines += ["", "## Details", ""]
        for r in detail:
            lines.append(f"### {r.title}")
            if not r.ran:
                lines.append("- NOT RUN (skipped or no results). Not run is never a pass.")
            lines += [f"- {f}" for f in r.failures]
            lines += [f"- CHANGED: {c}" for c in r.changed]
            lines.append("")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- main

def detect_event(env=None) -> str:
    """push | pull_request | fork_pr | local, from GITHUB_EVENT_NAME and the PR head repo."""
    env = os.environ if env is None else env
    name = env.get("GITHUB_EVENT_NAME", "")
    if not name:
        return "local"
    if name.startswith("pull_request"):
        try:
            ev = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
        except (KeyError, OSError, ValueError):
            # No payload to read: fail closed. A same-repo PR requires every row,
            # so a missing key FAILs instead of passing as a partial fork run.
            return "pull_request"
        try:
            head = ev["pull_request"]["head"]["repo"]["full_name"]
            base = ev["pull_request"]["base"]["repo"]["full_name"]
            return "fork_pr" if head != base else "pull_request"
        except (KeyError, TypeError):
            # head.repo is null when the fork was deleted. The payload says it is
            # not provably same-repo, so do not run the secrets-based suites.
            return "fork_pr"
    return name


def _expected_arg(value: str):
    cat, _, n = value.partition("=")
    if not cat or not n.isdigit():
        raise argparse.ArgumentTypeError(f"expected CATEGORY=COUNT with an integer count, got {value!r}")
    return cat, int(n)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "run":
        return run_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--junit", action="append", default=[])
    ap.add_argument("--vitest", action="append", default=[])
    ap.add_argument("--promptfoo", action="append", default=[], metavar="CATEGORY=PATH")
    ap.add_argument("--not-run", action="append", default=[], metavar="KEY",
                    help="mark a category as not run (e.g. trivy unavailable)")
    ap.add_argument("--repeat", type=int, default=None, help="N each Promptfoo case must have run")
    ap.add_argument("--expected-cases", type=_expected_arg, action="append", default=[],
                    metavar="CATEGORY=COUNT", help="override the count derived from the config")
    ap.add_argument("--config-root", default=str(Path(__file__).resolve().parent.parent),
                    help="repo root that holds the evals/ configs")
    ap.add_argument("--event", default=None)
    ap.add_argument("--meta", action="append", default=[], metavar="K=V")
    ap.add_argument("--out", default=None, help="directory for scorecard.md / scorecard.json")
    args = ap.parse_args(argv)

    missing_sources: list[str] = []

    def load(fn, path, *extra):
        try:
            return fn(path, *extra)
        except (OSError, ValueError, KeyError, ET.ParseError) as e:
            missing_sources.append(f"{path} ({type(e).__name__})")
            return []

    results: list[TestResult] = []
    for p in args.junit:
        results += load(parse_junit, p)
    for p in args.vitest:
        results += load(parse_vitest_json, p)
    buckets: dict[str, list] = {k: list(v) for k, v in categorize(results).items()}
    for spec in args.promptfoo:
        cat, _, path = spec.partition("=")
        buckets.setdefault(cat, []).extend(load(parse_promptfoo, path, cat))
        if cat == "direct_injection":  # the residual cases live in the same run
            buckets.setdefault("known_residuals", []).extend(load(parse_promptfoo, path, "known_residuals"))
    if args.promptfoo and args.repeat is None:
        ap.error("--repeat N is required when --promptfoo is given")
    flags = {k: False for k in args.not_run}
    expected: dict[str, int] = {}
    root = Path(args.config_root)
    for spec in args.promptfoo:
        cat = spec.partition("=")[0]
        try:
            plain, resid = config_case_counts(root / PROMPTFOO_SOURCES[cat if cat != "known_residuals" else "direct_injection"])
        except (ValueError, KeyError) as e:
            missing_sources.append(f"config for {cat} ({e})")
            plain = resid = 0
        for key, n in ((cat, plain),) + ((("known_residuals", resid),) if cat == "direct_injection" else ()):
            if n == 0:
                flags[key] = False  # no expected cases means we cannot tell if it ran
            expected[key] = n
    for k, v in args.expected_cases:  # explicit override
        expected[k] = v
        if v == 0:
            flags[k] = False
    meta = dict(m.partition("=")[::2] for m in args.meta)
    if missing_sources:
        meta["unreadable_sources"] = "; ".join(missing_sources)
    sc = score(CATEGORIES, buckets, flags, args.event or detect_event(), meta,
               repeat=args.repeat, expected_cases=expected)
    if missing_sources:
        sc.verdict = "FAIL"  # an unreadable source is never a pass

    md = render_md(sc)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "scorecard.md").write_text(md)
        (out / "scorecard.json").write_text(json.dumps(render_json(sc), indent=2) + "\n")
    print(md)
    return 0 if sc.verdict == "PASS" or sc.verdict.startswith("PASS (partial") else 1


# ------------------------------------------------------------------ runner
# `run` orchestrates every suite as a subprocess, then hands the outputs to main() above.
# It holds no scoring logic: a source that did not produce output is simply not passed
# on, so its rows come out as not run (and FAIL when required).

REPO = Path(__file__).resolve().parent.parent
PROMPTFOO_VERSION = "0.120.27"
PYTEST_DIRS = ["apps/api/tests", "tests/rls", "tests/scorecard"]
PROMPTFOO_RUNS = [  # (scoring category, config). The direct report also feeds known_residuals.
    ("direct_injection", "evals/promptfooconfig.yaml"),
    ("indirect_injection", "evals/promptfooconfig.indirect.yaml"),
    ("tool_injection", "evals/promptfooconfig.tools.yaml"),
]
# Seconds per source.
TIMEOUTS = {"pytest": 3600, "vitest": 900, "promptfoo": 7200, "lint": 300, "trivy": 1800}


def _sh(cmd, env=None, cwd=None, timeout=600):
    """Run one source. Returns (exit code, combined output); a timeout is exit code 124."""
    try:
        p = subprocess.run(cmd, cwd=cwd or REPO, env=env, timeout=timeout,
                           capture_output=True, text=True, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(map(str, cmd))}"
    except OSError as e:
        return 127, f"cannot run {cmd[0]}: {e}"


def git_dirty() -> bool:
    code, out = _sh(["git", "status", "--porcelain"])
    return code != 0 or bool(out.strip())


def _git_commit() -> str:
    code, out = _sh(["git", "rev-parse", "--short", "HEAD"])
    return out.strip() if code == 0 else "unknown"


def _app_header() -> dict[str, str]:
    """Model id and template ref come from the app, never hardcoded."""
    sys.path.insert(0, str(REPO / "apps" / "api"))
    try:
        from app.config import CHAT_MODEL
        from app.prompting import CHAT_TEMPLATE

        return {"chat_model": CHAT_MODEL, "template_ref": CHAT_TEMPLATE.ref}
    except Exception as e:  # noqa: BLE001 - a header gap is reported, not fatal
        return {"chat_model": f"unavailable ({type(e).__name__})", "template_ref": "unavailable"}


def _hygiene_junit(path: Path, ruff: str, trivy: str) -> None:
    """ruff and Trivy as junit testcases so the scoring path reads them like any test.
    Outcome is passed / failed / skipped (skipped = the tool could not run)."""
    def case(name, outcome):
        inner = {"passed": "", "failed": "<failure/>", "skipped": "<skipped/>"}[outcome]
        return f'<testcase classname="hygiene" file="scorecard/{name}" name="{name}">{inner}</testcase>'

    path.write_text('<testsuites><testsuite name="hygiene">'
                    + case("ruff", ruff) + case("trivy", trivy) + "</testsuite></testsuites>")


def run_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="scorecard.py run")
    ap.add_argument("--repeat", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--event", choices=["local", "push", "pull_request", "fork_pr"],
                    default=os.environ.get("SCORECARD_EVENT") or None)
    args = ap.parse_args(argv)
    event = args.event or detect_event()  # empty means: ask GitHub's env, else local
    # Read the tree state first: the previous scorecard in --out may be tracked
    # (evidence/week9), and deleting it below would make a clean tree look dirty.
    commit, dirty = _git_commit(), git_dirty()

    out = Path(args.out).resolve()
    raw = out / "raw"
    # Never score anything left by an earlier run: clear raw/ and the previous scorecard.
    shutil.rmtree(raw, ignore_errors=True)
    raw.mkdir(parents=True, exist_ok=True)
    for old in ("scorecard.md", "scorecard.json"):
        (out / old).unlink(missing_ok=True)
    py = os.environ.get("PYTHON") or "apps/api/.venv/bin/python"
    if "/" in py:
        py = os.path.abspath(REPO / py)  # not resolve(): a venv python is a symlink
    env = dict(os.environ, PYTHON=py, PROMPTFOO_PYTHON=py)
    notes: list[str] = []
    score_argv: list[str] = []

    def log(name, code, text):
        (raw / f"{name}.log").write_text(text)
        if code != 0:
            notes.append(f"{name} exit {code}")

    # pytest, once over every Python suite; per-file presence relies on the file attribute.
    junit = raw / "pytest.xml"
    code, text = _sh([py, "-m", "pytest", "-q", "-o", "junit_family=xunit1",
                      f"--junitxml={junit}", *PYTEST_DIRS], env=env, timeout=TIMEOUTS["pytest"])
    log("pytest", code, text)
    if code in (0, 1) and junit.exists():  # other codes: interrupted, usage error, timeout
        score_argv += ["--junit", str(junit)]

    # vitest
    vit = raw / "vitest.json"
    code, text = _sh(["npx", "vitest", "run", "--reporter=json", f"--outputFile={vit}"],
                     env=env, cwd=REPO / "apps" / "web", timeout=TIMEOUTS["vitest"])
    log("vitest", code, text)
    if code not in (124, 127) and vit.exists():
        score_argv += ["--vitest", str(vit)]

    # platform hygiene: ruff, then Trivy (needs Docker; without it Trivy is not run)
    code, text = _sh(["make", "lint"], env=env, timeout=TIMEOUTS["lint"])
    log("ruff", code, text)
    ruff = "passed" if code == 0 else "skipped" if code in (124, 127) else "failed"
    dcode, _ = _sh(["docker", "info"], env=env, timeout=60) if shutil.which("docker") else (127, "")
    if dcode == 0:
        code, text = _sh(["make", "trivy"], env=dict(env, TRIVY_OUT=".trivy.json"),
                         timeout=TIMEOUTS["trivy"])
        log("trivy", code, text)
        trivy = "passed" if code == 0 else "skipped" if code in (124, 127) else "failed"
    else:
        notes.append("trivy not run: Docker unavailable")
        trivy = "skipped"
    hyg = raw / "hygiene.xml"
    _hygiene_junit(hyg, ruff, trivy)
    score_argv += ["--junit", str(hyg)]

    # Promptfoo model suites. No key: do not run them and say why. Omitting --promptfoo
    # (rather than --not-run) leaves each row's deterministic half to be scored normally;
    # on a required event the empty model half then FAILs the row.
    if os.environ.get("ANTHROPIC_API_KEY") and event != "fork_pr":
        for cat, cfg in PROMPTFOO_RUNS:
            rep = raw / f"promptfoo-{cat}.json"
            code, text = _sh(["npx", f"promptfoo@{PROMPTFOO_VERSION}", "eval", "-c", cfg,
                              "--no-cache", "--no-share", "--repeat", str(args.repeat),
                              "-o", str(rep)], env=env, timeout=TIMEOUTS["promptfoo"])
            # promptfoo exits non-zero when a case fails; the report is what counts.
            log(f"promptfoo-{cat}", code, text)
            if code not in (124, 127) and rep.exists():
                score_argv += ["--promptfoo", f"{cat}={rep}"]
            else:
                notes.append(f"promptfoo {cat} produced no report (exit {code})")
    else:
        notes.append("model suites not run: ANTHROPIC_API_KEY is not set"
                     + ("" if event == "fork_pr" else " (required on this event, so FAIL)"))

    meta = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "commit": commit,
        "dirty": str(dirty).lower(),
        **_app_header(),
        "promptfoo_version": PROMPTFOO_VERSION,
        "repeat": str(args.repeat),
    }
    if notes:
        meta["notes"] = "; ".join(notes)
    for k, v in meta.items():
        score_argv += ["--meta", f"{k}={v}"]
    return main(score_argv + ["--repeat", str(args.repeat), "--event", event,
                              "--out", str(out)])


if __name__ == "__main__":
    sys.exit(main())
