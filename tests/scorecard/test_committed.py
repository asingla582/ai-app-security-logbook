"""Committed model evidence: CI makes no model calls and checks the local run is fresh.

Subprocess is mocked (the `fake` fixture from test_runner), so no network and no model.
A live run at repeat 10 writes model-latest.json into a temp dir; committed mode then
reads it back. Every way the evidence can be wrong must FAIL the model rows.
"""

import json
from pathlib import Path

import pytest
import scorecard as sc
from test_runner import fake  # noqa: F401 - pytest fixture, used by name

REPO = Path(__file__).resolve().parents[2]
MODEL_ROWS = ["direct_injection", "indirect_injection", "tool_injection", "known_residuals"]
REMEDY = "run `make scorecard` locally and commit evidence/scorecard/model-latest.json"


def _live(tmp, path, repeat=10):
    return sc.main(["run", "--repeat", str(repeat), "--out", str(tmp / "live"),
                    "--event", "local", "--model-evidence", str(path)])


def _committed(tmp, path, event="push"):
    code = sc.main(["run", "--model-source", "committed", "--out", str(tmp / "ci"),
                    "--event", event, "--model-evidence", str(path)])
    md = (tmp / "ci" / "scorecard.md").read_text()
    js = json.loads((tmp / "ci" / "scorecard.json").read_text())
    return code, md, js


def _rows(js):
    return {r["key"]: r for r in js["rows"]}


def _edit(path, fn):
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


@pytest.fixture
def evidence(fake, monkeypatch):  # noqa: F811 - the imported fixture, by name
    """A fresh model-latest.json from a (mocked) local live run at repeat 10. The key is
    then removed: committed mode must not need it."""
    calls, _, tmp = fake
    path = tmp / "model-latest.json"
    assert _live(tmp, path) == 0
    calls.clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    return path


def _assert_model_rows_fail(js, *needles):
    rows = _rows(js)
    assert js["verdict"] == "FAIL"
    for k in MODEL_ROWS:
        assert rows[k]["verdict"] == "FAIL", k
        text = " ".join(rows[k]["failures"])
        for n in needles:
            assert n in text, (k, n, text)
    # The deterministic rows still run live and still pass.
    assert rows["structural"]["verdict"] == "PASS"


def test_committed_mode_never_invokes_promptfoo(fake, evidence):  # noqa: F811
    calls, _, tmp = fake
    _committed(tmp, evidence)
    assert not any("eval" in c for c in calls)
    assert not any(any("promptfoo" in a for a in c) for c in calls)


def test_committed_fresh_evidence_passes_model_rows(fake, evidence):  # noqa: F811
    _, _, tmp = fake
    code, md, js = _committed(tmp, evidence)
    assert js["verdict"] == "PASS" and code == 0
    rows = _rows(js)
    for k in MODEL_ROWS:
        r = rows[k]
        assert r["ran"] is True and r["verdict"] == "PASS", k
        assert r["runs"] == 10 and r["attempts"] > 0
        assert "measured locally" in r["note"] and "at abc1234" in r["note"]
        assert "repeat 10" in r["note"] and "fingerprint match" in r["note"]
    assert js["meta"]["model_source"] == "committed"
    assert "- model_source: committed" in md
    assert "fingerprint match" in md


def test_committed_missing_file_fails(fake, evidence):  # noqa: F811
    _, _, tmp = fake
    evidence.unlink()
    code, _, js = _committed(tmp, evidence)
    assert code == 1
    _assert_model_rows_fail(js, "MISSING", REMEDY)
    assert all(_rows(js)[k]["ran"] is False for k in MODEL_ROWS)


def test_committed_malformed_json_fails(fake, evidence):  # noqa: F811
    _, _, tmp = fake
    evidence.write_text("{not json")
    code, _, js = _committed(tmp, evidence)
    assert code == 1
    _assert_model_rows_fail(js, "MALFORMED")


def test_committed_model_row_not_pass_fails(fake, evidence):  # noqa: F811
    _, _, tmp = fake

    def fail_tools(d):
        next(r for r in d["rows"] if r["key"] == "tool_injection")["verdict"] = "FAIL"

    _edit(evidence, fail_tools)
    code, _, js = _committed(tmp, evidence)
    assert code == 1
    _assert_model_rows_fail(js, "tool_injection", "FAIL")


def test_committed_case_failure_fails_even_when_row_says_pass(fake, evidence):  # noqa: F811
    """The committed cases are rescored, not trusted: a failed attempt under a PASS label
    still FAILs the row."""
    _, _, tmp = fake

    def one_bad_case(d):
        row = next(r for r in d["rows"] if r["key"] == "direct_injection")
        row["cases"][0]["passes"] -= 1
        row["cases"][0]["failures"] += 1

    _edit(evidence, one_bad_case)
    code, _, js = _committed(tmp, evidence)
    assert code == 1 and _rows(js)["direct_injection"]["verdict"] == "FAIL"


def test_committed_dropped_case_is_partial_and_fails(fake, evidence):  # noqa: F811
    _, _, tmp = fake

    def drop(d):
        next(r for r in d["rows"] if r["key"] == "tool_injection")["cases"].pop()

    _edit(evidence, drop)
    code, _, js = _committed(tmp, evidence)
    row = _rows(js)["tool_injection"]
    assert code == 1 and row["verdict"] == "FAIL"
    assert any("PARTIAL RUN" in f for f in row["failures"])


def test_committed_duplicate_case_cannot_pad_the_count(fake, evidence):  # noqa: F811
    """Swap a case for a copy of another: the count still matches the config, but one
    real case is gone. Duplicate keys are malformed, never a pass."""
    _, _, tmp = fake

    def dup(d):
        cases = next(r for r in d["rows"] if r["key"] == "direct_injection")["cases"]
        cases[-1] = dict(cases[0])

    _edit(evidence, dup)
    code, _, js = _committed(tmp, evidence)
    assert code == 1
    _assert_model_rows_fail(js, "MALFORMED", "duplicate")


def test_committed_repeat_below_10_fails(fake):  # noqa: F811
    calls, _, tmp = fake
    path = tmp / "model-latest.json"
    _live(tmp, path, repeat=3)  # a real repeat-3 run, not an edited header
    code, _, js = _committed(tmp, path)
    assert code == 1
    _assert_model_rows_fail(js, "repeat 3", "10")


def test_committed_stale_evals_hash_fails_naming_field(fake, evidence):  # noqa: F811
    _, _, tmp = fake
    _edit(evidence, lambda d: d["header"].update(evals_hash="0" * 64))
    code, _, js = _committed(tmp, evidence)
    assert code == 1
    _assert_model_rows_fail(js, "STALE: evals_hash changed", REMEDY)
    for k in MODEL_ROWS:
        assert "template_ref" not in " ".join(_rows(js)[k]["failures"])


def test_committed_stale_template_ref_fails(fake, evidence):  # noqa: F811
    _, _, tmp = fake
    _edit(evidence, lambda d: d["header"].update(template_ref="chat.v5@deadbeef"))
    code, _, js = _committed(tmp, evidence)
    assert code == 1
    _assert_model_rows_fail(js, "STALE: template_ref changed")


def test_committed_missing_fingerprint_field_fails(fake, evidence):  # noqa: F811
    """Run 2's evidence predates evals_hash/app_prompt_hash: it must read as stale."""
    _, _, tmp = fake

    def strip(d):
        del d["header"]["evals_hash"]
        del d["header"]["app_prompt_hash"]

    _edit(evidence, strip)
    code, _, js = _committed(tmp, evidence)
    assert code == 1
    _assert_model_rows_fail(js, "evals_hash", "app_prompt_hash", "STALE")


def test_committed_unavailable_fingerprint_fails(fake, monkeypatch):  # noqa: F811
    """If the app header cannot be read, both sides say "unavailable": equal, not fresh."""
    _, _, tmp = fake
    monkeypatch.setattr(sc, "_app_header", lambda: {
        "chat_model": "unavailable (ImportError)", "template_ref": "unavailable"})
    path = tmp / "model-latest.json"
    _live(tmp, path)
    code, _, js = _committed(tmp, path)
    assert code == 1
    _assert_model_rows_fail(js, "unavailable")


def test_committed_fork_pr_gets_the_full_check(fake, evidence):  # noqa: F811
    """No key is needed, so a fork PR is not excused: no partial verdict in committed mode."""
    _, _, tmp = fake
    evidence.unlink()
    code, _, js = _committed(tmp, evidence, event="fork_pr")
    assert code == 1 and js["verdict"] == "FAIL"
    assert all(_rows(js)[k]["required"] for k in MODEL_ROWS)


def test_committed_fresh_fork_pr_passes(fake, evidence):  # noqa: F811
    _, _, tmp = fake
    code, _, js = _committed(tmp, evidence, event="fork_pr")
    assert code == 0 and js["verdict"] == "PASS"


def test_live_run_writes_model_latest_with_fingerprint(fake):  # noqa: F811
    _, _, tmp = fake
    path = tmp / "model-latest.json"
    assert _live(tmp, path) == 0
    data = json.loads(path.read_text())
    head = data["header"]
    now = sc.current_fingerprint()
    for f in sc.FINGERPRINT_FIELDS:
        assert head[f] == now[f], f
    assert len(head["evals_hash"]) == 64 and len(head["app_prompt_hash"]) == 64
    assert head["repeat"] == "10" and head["model_source"] == "live"
    assert head["commit"] == "abc1234" and head["date"]
    rows = {r["key"]: r for r in data["rows"]}
    assert sorted(rows) == sorted(MODEL_ROWS)
    for k in MODEL_ROWS:
        assert rows[k]["verdict"] == "PASS" and rows[k]["cases"], k
        assert all(c["attempts"] == 10 for c in rows[k]["cases"])
    # The weekly evidence is still written as before.
    assert (tmp / "live" / "scorecard.md").exists()
    live = json.loads((tmp / "live" / "scorecard.json").read_text())
    assert live["meta"]["evals_hash"] == now["evals_hash"]


def test_live_run_without_model_evidence_writes_nothing(fake):  # noqa: F811
    _, _, tmp = fake
    sc.main(["run", "--repeat", "3", "--out", str(tmp / "o"), "--event", "push"])
    assert not list(tmp.rglob("model-latest.json"))


def test_live_mode_requires_repeat(fake):  # noqa: F811
    _, _, tmp = fake
    with pytest.raises(SystemExit):
        sc.main(["run", "--out", str(tmp / "o"), "--event", "push"])


def _tree(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_evals_hash_changes_when_an_eval_yaml_changes_and_ignores_readme(tmp_path):
    _tree(tmp_path, {
        "evals/promptfooconfig.yaml": "tests: []\n",
        "evals/chat_prompt.py": "x = 1\n",
        "evals/no_verbatim_leak.js": "module.exports = 1\n",
        "evals/README.md": "notes\n",
        "evals/__pycache__/chat_prompt.cpython-312.pyc": "bytecode",
        "evals/__pycache__/stray.py": "y = 2\n",
    })
    h0 = sc.evals_hash(tmp_path)
    assert len(h0) == 64
    (tmp_path / "evals/README.md").write_text("rewritten notes\n")
    (tmp_path / "evals/__pycache__/stray.py").write_text("y = 3\n")
    assert sc.evals_hash(tmp_path) == h0
    (tmp_path / "evals/promptfooconfig.yaml").write_text("tests: [1]\n")
    h1 = sc.evals_hash(tmp_path)
    assert h1 != h0
    (tmp_path / "evals/new-suite.yaml").write_text("tests: []\n")
    assert sc.evals_hash(tmp_path) != h1
    # A rename with identical bytes is still a change: the path is hashed too.
    (tmp_path / "evals/new-suite.yaml").rename(tmp_path / "evals/other-suite.yaml")
    assert sc.evals_hash(tmp_path) not in (h0, h1)


def test_app_prompt_hash_covers_templates_prompting_and_gateway(tmp_path):
    _tree(tmp_path, {
        "apps/api/app/prompts/chat/v6.md": "be safe\n",
        "apps/api/app/prompting.py": "ACTIVE = 6\n",
        "apps/api/app/gateway.py": "def call(): ...\n",
        "apps/api/app/other.py": "unrelated\n",
    })
    h0 = sc.app_prompt_hash(tmp_path)
    (tmp_path / "apps/api/app/other.py").write_text("changed\n")
    assert sc.app_prompt_hash(tmp_path) == h0
    for rel in ("apps/api/app/prompts/chat/v6.md", "apps/api/app/prompting.py",
                "apps/api/app/gateway.py"):
        before = sc.app_prompt_hash(tmp_path)
        (tmp_path / rel).write_text("edited\n")
        assert sc.app_prompt_hash(tmp_path) != before, rel


def test_real_tree_hashes_are_stable():
    assert sc.evals_hash(REPO) == sc.evals_hash(REPO)
    assert sc.app_prompt_hash(REPO) == sc.app_prompt_hash(REPO)


def _recipe(makefile: str, target: str) -> str:
    lines = makefile.splitlines()
    i = lines.index(f"{target}:")
    body = []
    for ln in lines[i + 1:]:
        if not ln.startswith("\t"):
            break
        body.append(ln)
    return "\n".join(body)


def test_scorecard_ci_target_uses_committed_mode():
    mk = (REPO / "Makefile").read_text()
    ci = _recipe(mk, "scorecard-ci")
    assert "--model-source committed" in ci
    assert "promptfoo" not in ci and "--repeat" not in ci
    local = _recipe(mk, "scorecard")
    assert "--repeat 10" in local
    assert "--model-evidence evidence/scorecard/model-latest.json" in local
    assert "--model-source committed" not in local
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "ANTHROPIC_API_KEY" not in workflow
    assert "make scorecard-ci" in workflow


def _app_imports(text: str, lazy: bool) -> set[str]:
    """app modules imported by `text`. lazy=True also counts imports inside functions."""
    import re

    indent = r"\s*" if lazy else ""
    mods = set(re.findall(rf"^{indent}from app\.(\w+) import", text, re.M))
    mods |= set(re.findall(rf"^{indent}from \.(\w+) import", text, re.M))
    return mods


def test_app_prompt_hash_covers_every_app_module_the_eval_providers_load():
    """The model suites load app modules through evals/*.py. A module in that import
    closure that is not hashed could change what the model sees without making the
    committed evidence stale. The providers import app lazily, so every import in
    evals/*.py counts; inside app, only module-level imports (tools.py imports its
    executors inside the executor functions, which the suites never call: they score
    proposals)."""
    app = REPO / "apps" / "api" / "app"
    todo = set()
    for p in (REPO / "evals").glob("*.py"):
        todo |= _app_imports(p.read_text(), lazy=True)
    closure: set[str] = set()
    while todo:
        m = todo.pop()
        if m in closure:
            continue
        closure.add(m)
        todo |= _app_imports((app / f"{m}.py").read_text(), lazy=False)
    hashed = {Path(f).stem for f in sc.APP_PROMPT_FILES}
    assert closure and closure - hashed == set()
