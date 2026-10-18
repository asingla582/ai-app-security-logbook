"""Scorecard core: parse results, map each into one category, score, render.

Pure parsing, scoring and rendering (no subprocess calls). The one rule behind all of
it: a category that did not run is never green. Skipped means not run, zero results
means not run, and a required row that did not run fails the verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorecard_config import (
    CATEGORIES,
    FILE_MAP,
    FORK_PR_OPTIONAL,
    NAME_OVERRIDES,
    RESIDUAL_DESCRIPTION_PREFIX,
    Category,
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
    errors: int

    @property
    def passed(self) -> bool:
        return self.attempts > 0 and self.passes == self.attempts and self.errors == 0


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


@dataclass
class Scorecard:
    verdict: str
    event: str
    rows: list[Row]
    meta: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- parsers

def _junit_file(tc: ET.Element) -> str:
    f = tc.get("file")
    if f:
        return f.replace("\\", "/")
    # classname is the dotted module path, possibly ending in a class name.
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
        for a in tr.get("assertionResults", []):
            outcome: Outcome = _VITEST.get(a.get("status"), "skipped")  # type: ignore[assignment]
            out.append(
                TestResult(
                    tr.get("name", "").replace("\\", "/"),
                    a.get("fullName") or a.get("title") or "",
                    outcome,
                )
            )
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
        g = grouped.setdefault(key, [desc, 0, 0, 0])
        g[1] += 1
        if item.get("error"):
            g[3] += 1  # an error is a failed attempt, never a pass
        elif item.get("success") is True:
            g[2] += 1
    return [(g[0], CaseResult(k, g[1], g[2], g[3])) for k, g in grouped.items()]


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

def _lookup(file: str, file_map: dict[str, str]) -> str | None:
    f = file.replace("\\", "/")
    if f in file_map:
        return file_map[f]
    hits = [
        p for p in file_map
        if p.endswith("/" + f) or f.endswith("/" + p)
    ]
    # Prefer the most specific (longest) path if several match.
    return file_map[max(hits, key=len)] if hits else None


def categorize(results, file_map=FILE_MAP, overrides=NAME_OVERRIDES) -> dict[str, list[TestResult]]:
    """Every result lands in exactly one bucket. Name prefix wins, then file, then
    other_deterministic."""
    buckets: dict[str, list[TestResult]] = {}
    for r in results:
        key = next((k for prefix, k in overrides if r.name.startswith(prefix)), None)
        key = key or _lookup(r.file, file_map) or FALLBACK_KEY
        buckets.setdefault(key, []).append(r)
    return buckets


# ----------------------------------------------------------------- scoring

def _row(cat: Category, items, ran_flag: bool, required: bool) -> Row:
    tests = [i for i in items if isinstance(i, TestResult)]
    cases = [i for i in items if isinstance(i, CaseResult)]
    executed = [t for t in tests if t.outcome != "skipped"]
    attempts = sum(c.attempts for c in cases)
    runs = len(executed) + attempts
    total = len(tests) + len(cases)
    passed = sum(t.outcome == "passed" for t in tests) + sum(c.passed for c in cases)
    failures = [
        f"{t.file}::{t.name} ({t.outcome})" for t in tests if t.outcome != "passed"
    ] + [
        f"{c.key} ({c.passes}/{c.attempts} passes, {c.errors} errors)"
        for c in cases if not c.passed
    ]
    if cat.kind == "model":
        has_run = attempts > 0
    else:
        has_run = len(executed) > 0 or attempts > 0
    ran = bool(ran_flag) and has_run
    sources = sorted({t.file for t in tests if t.file})

    if not ran:
        verdict = "FAIL" if required else "NOT RUN"
    elif cat.kind == "watch":
        verdict = "CHANGED" if failures else "PASS"
    else:
        verdict = "PASS" if total > 0 and passed == total else "FAIL"
    return Row(cat.key, cat.title, cat.kind, cat.threshold, required, ran, runs,
               passed, total, verdict, failures, sources)


def score(categories, buckets, ran_flags, event, meta=None) -> Scorecard:
    ran_flags = ran_flags or {}
    rows = []
    for cat in categories:
        required = cat.required and not (event == "fork_pr" and cat.key in FORK_PR_OPTIONAL)
        rows.append(_row(cat, buckets.get(cat.key, []), ran_flags.get(cat.key, True), required))
    if any(r.verdict == "FAIL" for r in rows):  # a non-required row only FAILs if it ran
        verdict = "FAIL"
    elif event == "fork_pr" and any(r.key in FORK_PR_OPTIONAL and not r.ran for r in rows):
        verdict = PARTIAL_FORK
    else:
        verdict = "PASS"
    return Scorecard(verdict, event, rows, dict(meta or {}))


# --------------------------------------------------------------- rendering

def render_json(sc: Scorecard) -> dict:
    return {
        "verdict": sc.verdict,
        "event": sc.event,
        "meta": sc.meta,
        "rows": [
            {
                "key": r.key, "title": r.title, "kind": r.kind, "required": r.required,
                "ran": r.ran, "runs": r.runs, "passed": r.passed, "total": r.total,
                "threshold": r.threshold, "verdict": r.verdict,
                "failures": r.failures, "sources": r.sources,
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
        "| Category | Kind | Ran | Runs | Passed/Total | Threshold | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sc.rows:
        lines.append(
            f"| {r.title} | {r.kind} | {'yes' if r.ran else 'NO'} | {r.runs} | "
            f"{r.passed}/{r.total} | {r.threshold} | {r.verdict} |"
        )
    detail = [r for r in sc.rows if r.failures or not r.ran]
    if detail:
        lines += ["", "## Details", ""]
        for r in detail:
            lines.append(f"### {r.title}")
            if not r.ran:
                lines.append("- NOT RUN (skipped or no results). Not run is never a pass.")
            lines += [f"- {f}" for f in r.failures]
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
            head = ev["pull_request"]["head"]["repo"]["full_name"]
            base = ev["pull_request"]["base"]["repo"]["full_name"]
            return "fork_pr" if head != base else "pull_request"
        except (KeyError, OSError, ValueError):
            return "fork_pr"  # cannot prove same-repo, so do not grant secrets-based rows
    return name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--junit", action="append", default=[])
    ap.add_argument("--vitest", action="append", default=[])
    ap.add_argument("--promptfoo", action="append", default=[], metavar="CATEGORY=PATH")
    ap.add_argument("--not-run", action="append", default=[], metavar="KEY",
                    help="mark a category as not run (e.g. trivy unavailable)")
    ap.add_argument("--event", default=None)
    ap.add_argument("--meta", action="append", default=[], metavar="K=V")
    ap.add_argument("--out", default=None, help="directory for scorecard.md / scorecard.json")
    args = ap.parse_args(argv)

    results: list[TestResult] = []
    for p in args.junit:
        results += parse_junit(p)
    for p in args.vitest:
        results += parse_vitest_json(p)
    buckets: dict[str, list] = {k: list(v) for k, v in categorize(results).items()}
    for spec in args.promptfoo:
        cat, _, path = spec.partition("=")
        buckets.setdefault(cat, []).extend(parse_promptfoo(path, cat))
        if cat == "direct_injection":  # the residual cases live in the same run
            buckets.setdefault("known_residuals", []).extend(parse_promptfoo(path, "known_residuals"))
    flags = {k: False for k in args.not_run}
    meta = dict(m.partition("=")[::2] for m in args.meta)
    sc = score(CATEGORIES, buckets, flags, args.event or detect_event(), meta)

    md = render_md(sc)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "scorecard.md").write_text(md)
        (out / "scorecard.json").write_text(json.dumps(render_json(sc), indent=2) + "\n")
    print(md)
    return 0 if sc.verdict == "PASS" or sc.verdict.startswith("PASS (partial") else 1


if __name__ == "__main__":
    sys.exit(main())
