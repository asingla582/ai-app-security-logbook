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
    # hashed_status: what `git status ... -- <hashed paths>` reports (live evidence check)
    state = {"docker": True, "dirty": False, "fail": {}, "bad_pf": False, "hashed_status": ""}

    def name_of(cmd):
        if "pytest" in cmd:
            return "pytest"
        if "vitest" in cmd:
            return "vitest"
        if "eval" in cmd:
            return "eval"
        if cmd[:2] == ["make", "lint"]:
            return "lint"
        if cmd[:2] == ["make", "trivy"]:
            return "trivy"
        return None

    def run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        mode = state["fail"].get(name_of(cmd))
        if mode == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1)
        if mode == "missing":
            raise FileNotFoundError(cmd[0])
        rc = mode if isinstance(mode, int) else 0
        if cmd[:2] == ["git", "status"] and "--" in cmd:
            return subprocess.CompletedProcess(cmd, 0, state["hashed_status"], "")
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
            if state["bad_pf"]:
                Path(cmd[cmd.index("-o") + 1]).write_text("{not json")
            else:
                _promptfoo(cmd[cmd.index("-o") + 1], cmd[cmd.index("-c") + 1])
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(sc.subprocess, "run", run)
    monkeypatch.setattr(sc.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("PYTHON", "python")
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    return calls, state, tmp_path


def _go(tmp_path, event="push", repeat=3):
    ev = ["--event", event] if event else []
    code = sc.main(["run", "--repeat", str(repeat), "--out", str(tmp_path / "o"), *ev])
    md = (tmp_path / "o" / "scorecard.md").read_text()
    js = json.loads((tmp_path / "o" / "scorecard.json").read_text())
    return code, md, js


def test_header_records_dirty_tree(fake):
    _, state, tmp = fake
    state["dirty"] = True
    _, md, js = _go(tmp)
    assert js["meta"]["dirty"] == "true" and "- dirty: true" in md
    for k in ("date", "commit", "chat_model", "template_ref", "promptfoo_version", "repeat",
              "grader_model"):
        assert js["meta"][k]
    assert f"- grader_model: {js['meta']['grader_model']}" in md
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


def _rows(js):
    return {r["key"]: r for r in js["rows"]}


def test_stale_outputs_are_never_scored(fake):
    """Run 1 passes and leaves green files; in run 2 every source times out."""
    _, state, tmp = fake
    _, _, js1 = _go(tmp)
    assert js1["verdict"] == "PASS"
    state["fail"] = {k: "timeout" for k in ("pytest", "vitest", "eval", "lint", "trivy")}
    code, _, js = _go(tmp)
    assert js["verdict"] == "FAIL" and code == 1
    rows = _rows(js)
    assert all(rows[k]["ran"] is False for k in MODEL_KEYS)
    assert rows["structural"]["ran"] is False


def test_old_scorecard_removed_when_run_crashes(fake, monkeypatch):
    _, _, tmp = fake
    _go(tmp)
    monkeypatch.setattr(sc, "main", lambda argv: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        sc.run_main(["--repeat", "3", "--out", str(tmp / "o"), "--event", "push"])
    assert not (tmp / "o" / "scorecard.md").exists()
    assert not (tmp / "o" / "scorecard.json").exists()


def test_stale_file_ignored_when_source_times_out(fake):
    """Defense in depth: a stale file exists, but the source timed out."""
    _, state, tmp = fake
    state["fail"] = {"pytest": "timeout"}
    seed = tmp / "o" / "raw"
    seed.mkdir(parents=True)
    _junit(seed / "pytest.xml")
    _, _, js = _go(tmp)
    assert js["verdict"] == "FAIL"
    assert _rows(js)["structural"]["ran"] is False


def test_pytest_exit_outside_0_1_is_not_run(fake):
    _, state, tmp = fake
    state["fail"] = {"pytest": 2}  # e.g. interrupted / usage error, though junit exists
    _, _, js = _go(tmp)
    assert js["verdict"] == "FAIL"
    assert _rows(js)["structural"]["ran"] is False


def test_launch_failure_of_vitest_fails(fake):
    _, state, tmp = fake
    state["fail"] = {"vitest": "missing"}
    _, _, js = _go(tmp)
    assert js["verdict"] == "FAIL"
    gate = _rows(js)["approval_gate"]
    assert gate["verdict"] == "FAIL" and any("MISSING" in f for f in gate["failures"])


def test_promptfoo_timeout_fails_model_rows(fake):
    _, state, tmp = fake
    state["fail"] = {"eval": "timeout"}
    _, _, js = _go(tmp)
    rows = _rows(js)
    assert js["verdict"] == "FAIL"
    assert all(rows[k]["ran"] is False for k in MODEL_KEYS)


def test_ruff_nonzero_fails_hygiene(fake):
    _, state, tmp = fake
    state["fail"] = {"lint": 1}
    _, _, js = _go(tmp)
    hyg = _rows(js)["platform_hygiene"]
    assert js["verdict"] == "FAIL" and hyg["verdict"] == "FAIL"


def test_ruff_launch_failure_is_not_run(fake):
    _, state, tmp = fake
    state["fail"] = {"lint": "missing"}
    _, _, js = _go(tmp)
    assert js["verdict"] == "FAIL"
    assert _rows(js)["platform_hygiene"]["verdict"] == "FAIL"


def test_invalid_promptfoo_report_fails(fake):
    _, state, tmp = fake
    state["bad_pf"] = True
    _, _, js = _go(tmp)
    assert js["verdict"] == "FAIL"
    assert "unreadable_sources" in js["meta"]


def test_promptfoo_nonzero_exit_is_noted(fake):
    _, state, tmp = fake
    state["fail"] = {"eval": 100}
    _, _, js = _go(tmp)
    assert "promptfoo" in js["meta"]["notes"] and "100" in js["meta"]["notes"]


def _gh_event(tmp, head, base="o/r"):
    p = tmp / "event.json"
    p.write_text(json.dumps({"pull_request": {"head": {"repo": {"full_name": head}},
                                              "base": {"repo": {"full_name": base}}}}))
    return str(p)


def test_event_resolved_from_github_fork_pr(fake, monkeypatch):
    calls, _, tmp = fake
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", _gh_event(tmp, "fork/r"))
    # a key may exist on a fork run; secrets must not be used for model suites anyway
    code, _, js = _go(tmp, event="")
    assert js["event"] == "fork_pr"
    assert not any("eval" in c for c in calls)
    assert js["verdict"].startswith("PASS (partial") and code == 0


def test_event_resolved_from_github_push(fake, monkeypatch):
    _, _, tmp = fake
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    _, _, js = _go(tmp, event="")
    assert js["event"] == "push"


def test_event_defaults_local_without_github(fake):
    _, _, tmp = fake
    _, _, js = _go(tmp, event="")
    assert js["event"] == "local"


def test_dirty_flag_read_before_tracked_outputs_are_deleted(fake, monkeypatch):
    """--out holds tracked scorecard files (evidence/week9). The runner deletes them
    before scoring; git status taken after that would report a clean tree as dirty."""
    _, _, tmp = fake
    out = tmp / "o"
    out.mkdir()
    tracked = [out / "scorecard.md", out / "scorecard.json"]
    for f in tracked:
        f.write_text("committed\n")
    real_run = sc.subprocess.run

    def run(cmd, **kw):
        if [str(c) for c in cmd[:2]] == ["git", "status"]:
            gone = [f for f in tracked if not f.exists()]
            text = "".join(f" D {f.name}\n" for f in gone)
            return subprocess.CompletedProcess(cmd, 0, text, "")
        return real_run(cmd, **kw)

    monkeypatch.setattr(sc.subprocess, "run", run)
    _, md, js = _go(tmp)
    assert js["meta"]["dirty"] == "false" and "- dirty: false" in md


def test_prompt_pin_problem_fails_the_scorecard(fake, monkeypatch):
    _, _, tmp = fake
    monkeypatch.setattr(sc, "prompt_pin_problems",
                        lambda *a, **k: ["evals/promptfooconfig.yaml pins chat_v6, active is 7"])
    code, md, js = _go(tmp)
    row = _rows(js)["platform_hygiene"]
    assert row["verdict"] == "FAIL" and js["verdict"] == "FAIL" and code == 1
    assert any("prompt_pin" in f for f in row["failures"])
    assert "pins chat_v6" in js["meta"]["notes"]


def test_prompt_pin_case_is_required():
    assert FILE_MAP["scorecard/prompt_pin"] == "platform_hygiene"
