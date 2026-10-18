"""Scorecard core: parse results, map each into one category, score, render.

Pure parsing, scoring and rendering (no subprocess calls). The one rule behind all of
it: a category that did not run is never green. Skipped means not run, zero results
means not run, and a required row that did not run fails the verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorecard_config import (
    ACTIVE_PROMPT_FUNCTIONS,
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
    note: str = ""  # where the model half's evidence came from (committed mode)


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

def _row(cat, items, ran_flag, event, repeat, expected, paths, missing, pins=False,
         fork_optional=True, extra=(), note="") -> Row:
    tests = [i for i in items if isinstance(i, TestResult)]
    cases = [i for i in items if isinstance(i, CaseResult)]
    if cases and repeat is None:
        raise ValueError("score() needs the expected repeat N when promptfoo cases are present")
    watch = cat.kind == "watch"
    has_half = cat.key in FORK_PR_OPTIONAL  # has a Promptfoo half
    excused = event == "fork_pr" and fork_optional  # fork PR: the model half is optional
    model_required = has_half and not excused
    executed = [t for t in tests if t.outcome != "skipped"]
    hard: list[str] = list(extra)  # caller-supplied failures, e.g. stale committed evidence
    changed: list[str] = []

    # Deterministic half: required on every event, scored independently of Promptfoo.
    for t in tests:
        label = f"{t.file}::{t.name} ({t.outcome})"
        if t.outcome == "passed":
            continue
        if watch and t.outcome == "failed":
            # a completed test whose pinned behavior moved: report, do not gate
            changed.append(label)
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
               cat.required and not (excused and has_half),
               ran, runs, passed, total, verdict, hard, sources, attempts, changed,
               note if verdict == "PASS" else "")


def score(categories, buckets, ran_flags, event, meta=None, repeat=None,
          expected_cases=None, file_map=FILE_MAP, overrides=NAME_OVERRIDES,
          row_failures=None, row_notes=None, fork_optional=True) -> Scorecard:
    """`repeat` is the N every Promptfoo case must have run. `expected_cases` maps a
    category key to how many cases its run should contain. Every file in `file_map`
    must contribute at least one executed result, or its row FAILs.

    `row_failures` adds hard failures to a row (key -> messages); `row_notes` says where
    a row's evidence came from, shown only on a PASS. `fork_optional=False` removes the
    fork-PR exemption for the model halves (committed mode needs no secret)."""
    ran_flags = ran_flags or {}
    row_failures = row_failures or {}
    row_notes = row_notes or {}
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
                         event, repeat, expected_cases, paths, missing, cat.key in pinned,
                         fork_optional, row_failures.get(cat.key, ()),
                         row_notes.get(cat.key, "")))
    if any(r.verdict == "FAIL" for r in rows):
        verdict = "FAIL"
    elif (event == "fork_pr" and fork_optional
          and any(r.key in FORK_PR_OPTIONAL and not r.ran for r in rows)):
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
    resid = sum(
        str(c.get("description", "")).startswith(RESIDUAL_DESCRIPTION_PREFIX) for c in cases
    )
    return len(cases) - resid, resid


_PINNED = re.compile(r"chat_v(\d+)")


def prompt_pin_problems(root, active_version, configs) -> list[str]:
    """Static check: every gate config's prompt function serves the active chat
    template. A function pinned to another version, an unknown function, or a config
    that cannot be read is a problem. Empty list means the check passed."""
    import yaml

    if active_version is None:
        return ["active chat version unavailable (app.prompting did not import)"]
    problems: list[str] = []
    for cfg in configs:
        try:
            prompts = (yaml.safe_load((Path(root) / cfg).read_text()) or {}).get("prompts")
        except (OSError, AttributeError, yaml.YAMLError) as e:
            problems.append(f"{cfg}: cannot read prompts ({type(e).__name__})")
            continue
        if not prompts:
            problems.append(f"{cfg}: no prompts entry")
            continue
        for entry in prompts:
            ref = entry.get("id", "") if isinstance(entry, dict) else str(entry)
            fn = ref.rsplit(":", 1)[-1] if ref.startswith("file://") and ref.count(":") >= 2 else ""
            if fn in ACTIVE_PROMPT_FUNCTIONS:
                continue
            m = _PINNED.fullmatch(fn)
            if not m:
                problems.append(f"{cfg}: prompt {ref!r} has no known template version")
            elif int(m.group(1)) != active_version:
                problems.append(f"{cfg} pins {fn}, active is v{active_version}")
    return problems


def grader_model(root, configs) -> str:
    """The llm-rubric grader model(s), from each config's defaultTest.options.provider.
    Read from the configs, never hardcoded. A config with no grader adds nothing."""
    import yaml

    models: set[str] = set()
    for cfg in configs:
        try:
            data = yaml.safe_load((Path(root) / cfg).read_text()) or {}
            prov = ((data.get("defaultTest") or {}).get("options") or {}).get("provider")
        except (OSError, AttributeError, yaml.YAMLError) as e:
            return f"unavailable ({cfg}: {type(e).__name__})"
        if isinstance(prov, dict):
            prov = prov.get("id")
        if prov:
            models.add(str(prov).rsplit(":", 1)[-1])
    return ", ".join(sorted(models)) or "none"


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
                "note": r.note,
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
    detail = [r for r in sc.rows if r.failures or r.changed or not r.ran or r.note]
    if detail:
        lines += ["", "## Details", ""]
        for r in detail:
            lines.append(f"### {r.title}")
            if r.note:
                lines.append(f"- model evidence: {r.note}")
            if not r.ran:
                lines.append("- NOT RUN (skipped or no results). Not run is never a pass.")
            lines += [f"- {f}" for f in r.failures]
            lines += [f"- CHANGED: {c}" for c in r.changed]
            lines.append("")
    return "\n".join(lines) + "\n"


# ------------------------------------------------ committed model evidence
# CI makes no model calls. The model suites run locally (`make scorecard`, repeat 10),
# which writes evidence/scorecard/model-latest.json; CI reads it back and accepts it
# only if it is complete, all PASS, repeat >= 10, and its fingerprint equals the one
# computed from the tree being merged. Content-based, not commit-based: the evidence is
# committed after the run, so its commit can never be the commit under test.

MODEL_LATEST_REL = "evidence/scorecard/model-latest.json"
MIN_COMMITTED_REPEAT = 10
# The header fields that say what was measured. Each must be present in the committed
# header and equal the value computed now.
FINGERPRINT_FIELDS = ("template_ref", "chat_model", "grader_model", "promptfoo_version",
                      "evals_hash", "app_prompt_hash")
# Rows with a Promptfoo half, in scorecard order.
MODEL_ROWS = [c.key for c in CATEGORIES if c.key in FORK_PR_OPTIONAL]
REMEDY = f"run `make scorecard` locally and commit {MODEL_LATEST_REL}"
EVALS_SUFFIXES = (".yaml", ".yml", ".py", ".js")
# prompting.py and gateway.py, plus every other app module the eval providers
# (evals/*.py) load: tool schemas (tools.py), untrusted-content fences (provenance.py)
# and the model id / output cap (config.py) also shape what the model sees.
APP_PROMPT_FILES = tuple(f"apps/api/app/{m}.py" for m in (
    "prompting", "gateway", "tools", "provenance", "config"))


def _files_under(root: Path, sub: str, suffixes=None) -> list[str]:
    base = root / sub
    if not base.is_dir():
        return []
    out = []
    for p in base.rglob("*"):
        rel = p.relative_to(root)
        if not p.is_file() or "__pycache__" in rel.parts:
            continue
        if suffixes is not None and p.suffix not in suffixes:
            continue
        out.append(rel.as_posix())
    return out


def _hash_files(root: Path, rels) -> str:
    """sha256 over the sorted (path, bytes) pairs, length-framed so no two different
    trees can produce the same byte stream. A rename changes the hash."""
    h = hashlib.sha256()
    for rel in sorted(set(rels)):
        data = (root / rel).read_bytes()
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()


def evals_hash(root) -> str:
    """Every file under evals/ that affects scoring (*.yaml, *.yml, *.py, *.js). README
    and __pycache__ are excluded: they cannot change a verdict."""
    root = Path(root)
    return _hash_files(root, [r for r in _files_under(root, "evals", EVALS_SUFFIXES)
                              if Path(r).name.upper() != "README.MD"])


def app_prompt_hash(root) -> str:
    """The chat templates plus the app code the model suites load: what the model sees."""
    root = Path(root)
    rels = _files_under(root, "apps/api/app/prompts")
    rels += [f for f in APP_PROMPT_FILES if (root / f).is_file()]
    return _hash_files(root, rels)


@dataclass
class Committed:
    cases: dict[str, list[CaseResult]] | None  # None unless the evidence is fresh
    problems: list[str]
    note: str = ""
    repeat: int | None = None


def _case(d) -> CaseResult | None:
    if not isinstance(d, dict) or not isinstance(d.get("key"), str):
        return None
    vals = [d.get(f) for f in ("attempts", "passes", "errors", "failures")]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in vals):
        return None
    return CaseResult(d["key"], *vals)


def _usable(v) -> bool:
    return isinstance(v, str) and bool(v) and not v.startswith("unavailable")


def load_committed(path, current: dict[str, str]) -> Committed:
    """Read committed model evidence and decide if it is fresh for the tree whose
    fingerprint is `current`. Any problem means not fresh: no cases are returned, so
    the model rows cannot be scored as run. Every problem is reported, not just the first."""
    def fail(problems):
        return Committed(None, [f"{p}; {REMEDY}" for p in problems])

    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return fail([f"MISSING: {MODEL_LATEST_REL} not found (looked at {path})"])
    except OSError as e:
        return fail([f"MISSING: {path} unreadable ({type(e).__name__})"])
    try:
        data = json.loads(text)
    except ValueError as e:
        return fail([f"MALFORMED: {path} is not valid JSON ({e})"])
    if (not isinstance(data, dict) or not isinstance(data.get("header"), dict)
            or not isinstance(data.get("rows"), list)):
        return fail([f"MALFORMED: {path} has no header object and rows list"])
    head, problems = data["header"], []

    absent = [f for f in (*FINGERPRINT_FIELDS, "repeat") if f not in head]
    if absent:
        problems.append(f"STALE: committed header lacks {', '.join(absent)}")
    for f in FINGERPRINT_FIELDS:
        if f not in head:
            continue
        was, now = head[f], current.get(f)
        if not _usable(was):
            problems.append(f"STALE: committed {f} is unusable ({was!r})")
        elif not _usable(now):
            problems.append(f"STALE: {f} cannot be computed now ({now!r})")
        elif was != now:
            problems.append(f"STALE: {f} changed (committed {was!r}, now {now!r})")
    repeat = None
    if "repeat" in head:
        try:
            repeat = int(str(head["repeat"]))
        except ValueError:
            problems.append(f"MALFORMED: committed repeat {head['repeat']!r} is not an integer")
        else:
            if repeat < MIN_COMMITTED_REPEAT:
                problems.append(f"STALE: committed at repeat {repeat}, a release needs "
                                f"repeat >= {MIN_COMMITTED_REPEAT}")

    by_key: dict[str, dict] = {}
    for r in data["rows"]:
        if not isinstance(r, dict) or not isinstance(r.get("key"), str):
            problems.append("MALFORMED: a committed row has no key")
            continue
        if r["key"] in by_key:
            problems.append(f"MALFORMED: committed row {r['key']} appears twice")
        by_key[r["key"]] = r
    cases: dict[str, list[CaseResult]] = {}
    for k in MODEL_ROWS:
        r = by_key.get(k)
        if r is None:
            problems.append(f"MISSING: committed evidence has no {k} row")
            continue
        if r.get("verdict") != "PASS" or r.get("ran") is not True:
            problems.append(f"NOT PASS: committed {k} verdict is {r.get('verdict')!r}"
                            f" (ran={r.get('ran')!r})")
        raw = r.get("cases")
        parsed = [_case(c) for c in raw] if isinstance(raw, list) else [None]
        if any(c is None for c in parsed):
            problems.append(f"MALFORMED: committed {k} cases are not a list of case results")
            continue
        keys = [c.key for c in parsed]  # type: ignore[union-attr]
        if len(set(keys)) != len(keys):
            # a copied case could pad the count up to the config's and hide a lost one
            problems.append(f"MALFORMED: committed {k} has duplicate case keys")
            continue
        cases[k] = parsed  # type: ignore[assignment]
    if problems:
        return fail(problems)

    note = (f"measured locally {head.get('date', 'unknown date')} at "
            f"{head.get('commit', 'unknown commit')}, repeat {repeat}, fingerprint match")
    if str(head.get("dirty")).lower() == "true":
        note += " (dirty tree at measurement)"
    return Committed(cases, [], note, repeat)


def model_evidence(card: Scorecard, buckets) -> dict:
    """What a live run commits: the header (fingerprint included) and the model rows with
    their per-case results, so committed mode can rescore them, not just trust a label."""
    rows = []
    for r in card.rows:
        if r.key not in MODEL_ROWS:
            continue
        cases = [c for c in buckets.get(r.key, []) if isinstance(c, CaseResult)]
        rows.append({
            "key": r.key, "verdict": r.verdict, "ran": r.ran, "runs": r.runs,
            "attempts": r.attempts, "passed": r.passed, "total": r.total,
            "failures": r.failures, "changed": r.changed,
            "cases": [{"key": c.key, "attempts": c.attempts, "passes": c.passes,
                       "errors": c.errors, "failures": c.failures} for c in cases],
        })
    return {"schema": 1, "event": card.event, "verdict": card.verdict,
            "header": dict(card.meta), "rows": rows}


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
        raise argparse.ArgumentTypeError(
            f"expected CATEGORY=COUNT with an integer count, got {value!r}"
        )
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
    ap.add_argument("--committed", default=None, metavar="PATH",
                    help="score the model rows from committed evidence (no Promptfoo)")
    ap.add_argument("--write-model-evidence", default=None, metavar="PATH",
                    help="also write the model rows and fingerprint here (live runs)")
    args = ap.parse_args(argv)
    if args.committed and (args.promptfoo or args.repeat is not None):
        ap.error("--committed takes the model results and repeat from the committed file;"
                 " do not pass --promptfoo or --repeat with it")
    if args.committed and args.write_model_evidence:
        ap.error("--write-model-evidence is for live runs: committed results are never"
                 " re-stamped as new evidence")

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
            buckets.setdefault("known_residuals", []).extend(
                load(parse_promptfoo, path, "known_residuals")
            )
    if args.promptfoo and args.repeat is None:
        ap.error("--repeat N is required when --promptfoo is given")
    repeat = args.repeat
    model_cats = [spec.partition("=")[0] for spec in args.promptfoo]
    row_failures: dict[str, list[str]] = {}
    row_notes: dict[str, str] = {}
    meta = dict(m.partition("=")[::2] for m in args.meta)
    if args.committed:
        com = load_committed(args.committed, current_fingerprint())
        if com.cases is None:
            # Not fresh: no cases go in, so each model half is NOT RUN, and the reason
            # rides on the row. Never a pass.
            row_failures = {k: list(com.problems) for k in MODEL_ROWS}
            meta["model_evidence"] = "REJECTED: " + " | ".join(com.problems)
        else:
            for k, cases in com.cases.items():
                buckets.setdefault(k, []).extend(cases)
            repeat = com.repeat
            # Expected case counts come from the configs in this tree, as in a live run
            # (known_residuals is derived from the direct-injection config below).
            model_cats = [k for k in PROMPTFOO_SOURCES if k != "known_residuals"]
            row_notes = {k: com.note for k in MODEL_ROWS}
            meta["model_evidence"] = com.note
            meta["repeat"] = str(com.repeat)
    flags = {k: False for k in args.not_run}
    expected: dict[str, int] = {}
    root = Path(args.config_root)
    for cat in model_cats:
        try:
            src = cat if cat != "known_residuals" else "direct_injection"
            plain, resid = config_case_counts(root / PROMPTFOO_SOURCES[src])
        except (ValueError, KeyError) as e:
            missing_sources.append(f"config for {cat} ({e})")
            plain = resid = 0
        pairs = [(cat, plain)] + ([("known_residuals", resid)] if cat == "direct_injection" else [])
        for key, n in pairs:
            if n == 0:
                flags[key] = False  # no expected cases means we cannot tell if it ran
            expected[key] = n
    for k, v in args.expected_cases:  # explicit override
        expected[k] = v
        if v == 0:
            flags[k] = False
    if missing_sources:
        meta["unreadable_sources"] = "; ".join(missing_sources)
    sc = score(CATEGORIES, buckets, flags, args.event or detect_event(), meta,
               repeat=repeat, expected_cases=expected, row_failures=row_failures,
               row_notes=row_notes, fork_optional=not args.committed)
    if missing_sources:
        sc.verdict = "FAIL"  # an unreadable source is never a pass
    if args.write_model_evidence:
        dest = Path(args.write_model_evidence)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(model_evidence(sc, buckets), indent=2) + "\n")

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


def current_fingerprint() -> dict[str, str]:
    """What a model measurement depends on, computed from the working tree now. The
    same fields are recorded by every run and compared by committed mode."""
    try:
        hashes = {"evals_hash": evals_hash(REPO), "app_prompt_hash": app_prompt_hash(REPO)}
    except OSError as e:  # unreadable tree: report it, it can never match
        bad = f"unavailable ({type(e).__name__})"
        hashes = {"evals_hash": bad, "app_prompt_hash": bad}
    return {
        **_app_header(),
        "grader_model": grader_model(REPO, [cfg for _, cfg in PROMPTFOO_RUNS]),
        "promptfoo_version": PROMPTFOO_VERSION,
        **hashes,
    }


def _active_chat_version() -> int | None:
    sys.path.insert(0, str(REPO / "apps" / "api"))
    try:
        from app.prompting import ACTIVE_CHAT_VERSION

        return ACTIVE_CHAT_VERSION
    except Exception:  # reported by prompt_pin_problems as a FAIL, never a pass
        return None


def _hygiene_junit(path: Path, outcomes: dict[str, str]) -> None:
    """The runner's own checks (ruff, Trivy, prompt_pin) as junit testcases so the
    scoring path reads them like any test. Outcome is passed / failed / skipped
    (skipped = the tool could not run)."""
    def case(name, outcome):
        inner = {"passed": "", "failed": "<failure/>", "skipped": "<skipped/>"}[outcome]
        return (f'<testcase classname="hygiene" file="scorecard/{name}" name="{name}">'
                f"{inner}</testcase>")

    path.write_text('<testsuites><testsuite name="hygiene">'
                    + "".join(case(n, o) for n, o in outcomes.items())
                    + "</testsuite></testsuites>")


def run_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="scorecard.py run")
    ap.add_argument("--model-source", choices=["live", "committed"], default="live",
                    help="live: run Promptfoo (paid). committed: no model calls; check "
                         f"the committed evidence ({MODEL_LATEST_REL}) is fresh")
    ap.add_argument("--model-evidence", default=None, metavar="PATH",
                    help="live: also write the model rows + fingerprint here. committed: "
                         f"read them from here (default {MODEL_LATEST_REL})")
    ap.add_argument("--repeat", type=int, default=None,
                    help="live only: N runs per Promptfoo case")
    ap.add_argument("--out", required=True)
    ap.add_argument("--event", choices=["local", "push", "pull_request", "fork_pr"],
                    default=os.environ.get("SCORECARD_EVENT") or None)
    args = ap.parse_args(argv)
    committed = args.model_source == "committed"
    if committed and args.repeat is not None:
        ap.error("--repeat is for live runs; committed mode uses the committed evidence's repeat")
    if not committed and args.repeat is None:
        ap.error("--repeat is required with --model-source live")
    event = args.event or detect_event()  # empty means: ask GitHub's env, else local
    # Read the tree state first: the previous scorecard in --out may be tracked
    # (evidence/week9), and deleting it below would make a clean tree look dirty.
    commit, dirty = _git_commit(), git_dirty()
    # Fingerprint what is about to be measured, before any suite runs.
    fingerprint = current_fingerprint()
    evidence = Path(args.model_evidence or (REPO / MODEL_LATEST_REL)).resolve()
    if not committed and args.model_evidence:
        evidence.unlink(missing_ok=True)  # never leave an older run's evidence behind

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
    # Static: every gate config must score the active chat template (platform_hygiene row).
    pins = prompt_pin_problems(REPO, _active_chat_version(), [cfg for _, cfg in PROMPTFOO_RUNS])
    notes += [f"prompt_pin: {p}" for p in pins]
    hyg = raw / "hygiene.xml"
    _hygiene_junit(hyg, {"ruff": ruff, "trivy": trivy,
                         "prompt_pin": "failed" if pins else "passed"})
    score_argv += ["--junit", str(hyg)]

    # Promptfoo model suites. No key: do not run them and say why. Omitting --promptfoo
    # (rather than --not-run) leaves each row's deterministic half to be scored normally;
    # on a required event the empty model half then FAILs the row.
    if committed:
        # CI: never call a model. The model halves come from the committed evidence,
        # scored only if it is fresh for this tree (main --committed).
        notes.append("model suites not run here: model rows scored from committed "
                     "evidence, fingerprint-checked")
        score_argv += ["--committed", str(evidence)]
    elif os.environ.get("ANTHROPIC_API_KEY") and event != "fork_pr":
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
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "commit": commit,
        "dirty": str(dirty).lower(),
        "model_source": args.model_source,
        **fingerprint,
    }
    if not committed:
        meta["repeat"] = str(args.repeat)
    if notes:
        meta["notes"] = "; ".join(notes)
    for k, v in meta.items():
        score_argv += ["--meta", f"{k}={v}"]
    if committed:
        return main(score_argv + ["--event", event, "--out", str(out)])
    if args.model_evidence:
        score_argv += ["--write-model-evidence", str(evidence)]
    return main(score_argv + ["--repeat", str(args.repeat), "--event", event,
                              "--out", str(out)])


if __name__ == "__main__":
    sys.exit(main())
