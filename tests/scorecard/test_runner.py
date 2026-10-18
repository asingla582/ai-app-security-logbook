"""Runner tests: subprocess is mocked, no network, no real suites."""

import json
import subprocess
from pathlib import Path

import pytest
import scorecard as sc
from scorecard_config import FILE_MAP, NAME_OVERRIDES

MODEL_KEYS = ["direct_injection", "indirect_injection", "tool_injection"]


def _junit(path):
    cases = [(p, "t") for p in FILE_MAP if not p.startswith(("apps/web/", "scorecard/"))]
    cases += [(f, p + "x") for f, p, _ in NAME_OVERRIDES]
    cases = [(f, p if p == "t" else p + "x") for f, p in cases]
    body = "".join(f'<testcase classname="x" file="{f}" name="{n}"/>' for f, n in cases)
    Path(path).write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>")


def _vitest(path):
    results = [{"name": "/r/" + p, "status": "passed",
                "assertionResults": [{"status": "passed", "fullName": "t"}]}
               for p in FILE_MAP if p.startswith("apps/web/")]
    Path(path).write_text(json.dumps({"success": True, "testResults": results}))


def _promptfoo(path, cfg):
    from scorecard_config import RESIDUAL_DESCRIPTION_PREFIX as RP

    plain, resid = sc.config_case_counts(sc.REPO / cfg)
    rep = int(path_repeat[0])
    descs = [f"case {i}" for i in range(plain)] + [RP + f" {i}" for i in range(resid)]
    items = [{"testCase": {"description": d}, "vars": {"d": d}, "success": True}
             for d in descs for _ in range(rep)]
    Path(path).write_text(json.dumps({"results": {"results": items}}))


path_repeat = [3]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    state = {"docker": True, "dirty": False}

    def run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        ok = subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, " M x\n" if state["dirty"] else "", "")
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, "abc1234\n", "")
        if cmd[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(cmd, 0 if state["docker"] else 1, "", "")
        if "pytest" in cmd:
            _junit(next(c.split("=", 1)[1] for c in cmd if c.startswith("--junitxml=")))
        if "vitest" in cmd:
            _vitest(next(c.split("=", 1)[1] for c in cmd if c.startswith("--outputFile=")))
        if "eval" in cmd:
            path_repeat[0] = int(cmd[cmd.index("--repeat") + 1])
            _promptfoo(cmd[cmd.index("-o") + 1], cmd[cmd.index("-c") + 1])
        return ok

    monkeypatch.setattr(sc.subprocess, "run", run)
    monkeypatch.setattr(sc.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("PYTHON", "python")
    return calls, state, tmp_path


def _go(tmp_path, event="push", repeat=3):
    code = sc.main(["run", "--repeat", str(repeat), "--out", str(tmp_path / "o"), "--event", event])
    md = (tmp_path / "o" / "scorecard.md").read_text()
    js = json.loads((tmp_path / "o" / "scorecard.json").read_text())
    return code, md, js


def test_header_records_dirty_tree(fake):
    _, state, tmp = fake
    state["dirty"] = True
    _, md, js = _go(tmp)
    assert js["meta"]["dirty"] == "true" and "- dirty: true" in md
    for k in ("date", "commit", "chat_model", "template_ref", "promptfoo_version", "repeat"):
        assert js["meta"][k]
    assert js["event"] == "push"


def test_clean_run_passes_with_every_row_ran(fake):
    _, _, tmp = fake
    code, _, js = _go(tmp)
    assert js["verdict"] == "PASS" and code == 0
    assert all(r["ran"] for r in js["rows"])


def test_missing_key_on_required_event_fails(fake, monkeypatch):
    calls, _, tmp = fake
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    code, md, js = _go(tmp, "push")
    assert not any("promptfoo@0.120.27" in c for c in calls)
    rows = {r["key"]: r for r in js["rows"]}
    assert all(rows[k]["ran"] is False for k in MODEL_KEYS)
    assert js["verdict"] == "FAIL" and code == 1
    assert "ANTHROPIC_API_KEY" in md


def test_missing_key_on_fork_pr_is_partial(fake, monkeypatch):
    _, _, tmp = fake
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    code, _, js = _go(tmp, "fork_pr")
    rows = {r["key"]: r for r in js["rows"]}
    assert all(rows[k]["ran"] is False for k in MODEL_KEYS)
    assert js["verdict"].startswith("PASS (partial") and code == 0


def test_docker_unavailable_means_trivy_not_run_and_fails(fake):
    calls, state, tmp = fake
    state["docker"] = False
    _, _, js = _go(tmp)
    assert ["make", "trivy"] not in calls
    hyg = next(r for r in js["rows"] if r["key"] == "platform_hygiene")
    assert hyg["verdict"] == "FAIL" and js["verdict"] == "FAIL"


def test_runner_invokes_each_source_once(fake):
    calls, _, tmp = fake
    _go(tmp, repeat=3)
    pytest_calls = [c for c in calls if "pytest" in c]
    assert len(pytest_calls) == 1
    pc = pytest_calls[0]
    assert any(a.startswith("--junitxml=") for a in pc) and "junit_family=xunit1" in pc
    assert pc[-3:] == ["apps/api/tests", "tests/rls", "tests/scorecard"]
    vit = [c for c in calls if "vitest" in c]
    assert len(vit) == 1 and "--reporter=json" in vit[0]
    for cfg in ("evals/promptfooconfig.yaml", "evals/promptfooconfig.indirect.yaml",
                "evals/promptfooconfig.tools.yaml"):
        pf = [c for c in calls if "eval" in c and c[c.index("-c") + 1] == cfg]
        assert len(pf) == 1
        c = pf[0]
        assert c[c.index("--repeat") + 1] == "3" and "-o" in c
        assert "--no-cache" in c and "--no-share" in c and "promptfoo@0.120.27" in c
    assert calls.count(["make", "lint"]) == 1 and calls.count(["make", "trivy"]) == 1
