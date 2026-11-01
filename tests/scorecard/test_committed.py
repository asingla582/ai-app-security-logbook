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


def test_committed_mode_never_invokes_promptfoo(fake, evidence, monkeypatch):  # noqa: F811
    """With the key SET: the live no-key branch also skips Promptfoo, so only a run that
    has a key and still makes no model call proves committed mode never calls one."""
    calls, _, tmp = fake
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _, _, js = _committed(tmp, evidence)
    assert js["meta"]["model_source"] == "committed"
    assert calls, "the subprocess mock saw the deterministic suites"
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
        "apps/api/app/tools.py": "TOOLS = []\n",
        "apps/api/app/other.py": "unrelated\n",
    })
    h0 = sc.app_prompt_hash(tmp_path)
    (tmp_path / "apps/api/app/other.py").write_text("changed\n")
    assert sc.app_prompt_hash(tmp_path) == h0
    for rel in ("apps/api/app/prompts/chat/v6.md", "apps/api/app/prompting.py",
                "apps/api/app/gateway.py", "apps/api/app/tools.py"):
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
    mods = set(re.findall(rf"^{indent}from app\.(\w+)", text, re.M))
    mods |= set(re.findall(rf"^{indent}import app\.(\w+)", text, re.M))
    mods |= set(re.findall(rf"^{indent}from \.(\w+) import", text, re.M))
    for names in re.findall(rf"^{indent}from (?:app|\.) import ([\w, ()]+)", text, re.M):
        mods |= {n.strip(" ()").split(" as ")[0] for n in names.split(",") if n.strip(" ()")}
    return mods


def test_app_imports_regex_sees_every_import_form():
    text = ("import app.alpha\nfrom app.beta import x\nfrom app import gamma, delta as d\n"
            "from . import epsilon\nfrom .zeta import y\n    from app.eta import z\n")
    assert _app_imports(text, lazy=False) == {"alpha", "beta", "gamma", "delta", "epsilon",
                                              "zeta"}
    assert "eta" in _app_imports(text, lazy=True)


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


def test_committed_evidence_cannot_be_rewritten_as_new_evidence(tmp_path):
    """Reading committed evidence and writing model evidence in one call could re-stamp
    old results with a new header. The scorer refuses the combination."""
    with pytest.raises(SystemExit):
        sc.main(["--committed", str(tmp_path / "a.json"),
                 "--write-model-evidence", str(tmp_path / "b.json")])
    assert not (tmp_path / "b.json").exists()


# ------------------------------------------------------- fix round (review)

def test_dotfiles_and_non_md_under_prompts_do_not_change_the_hash(tmp_path):
    _tree(tmp_path, {"apps/api/app/prompts/chat/v6.md": "be safe\n",
                     "apps/api/app/prompting.py": "A = 1\n",
                     "evals/promptfooconfig.yaml": "tests: []\n"})
    a0, e0 = sc.app_prompt_hash(tmp_path), sc.evals_hash(tmp_path)
    _tree(tmp_path, {"apps/api/app/prompts/.DS_Store": "finder",
                     "apps/api/app/prompts/chat/.v6.md.swp": "vim swap",
                     "apps/api/app/prompts/chat/v6.md~": "backup",
                     "apps/api/app/prompts/chat/notes.txt": "scratch",
                     "evals/.DS_Store": "finder",
                     "evals/.hidden/x.yaml": "tests: [1]\n",
                     "evals/.scratch.yaml": "tests: [1]\n"})
    assert sc.app_prompt_hash(tmp_path) == a0
    assert sc.evals_hash(tmp_path) == e0


def test_gitignore_ignores_ds_store():
    assert ".DS_Store" in (REPO / ".gitignore").read_text().splitlines()


def test_untracked_eval_yaml_makes_live_run_refuse_to_write(fake, capsys):  # noqa: F811
    calls, state, tmp = fake
    state["hashed_status"] = "?? evals/scratch.yaml\n"
    path = tmp / "model-latest.json"
    code = _live(tmp, path)
    assert code == 1 and not path.exists()
    js = json.loads((tmp / "live" / "scorecard.json").read_text())
    assert js["verdict"] == "FAIL"
    assert "evals/scratch.yaml" in js["meta"]["model_evidence"]
    assert "NOT WRITTEN" in capsys.readouterr().err
    status = [c for c in calls if c[:2] == ["git", "status"] and "--" in c]
    assert status and "--untracked-files=all" in status[0]
    paths = status[0][status[0].index("--") + 1:]
    assert "evals" in paths and "apps/api/app/prompts" in paths
    assert set(sc.APP_PROMPT_FILES) <= set(paths)


def test_modified_tracked_hashed_file_refuses_but_unhashed_noise_does_not(fake):  # noqa: F811
    _, state, tmp = fake
    path = tmp / "model-latest.json"
    state["hashed_status"] = " M apps/api/app/tools.py\n"
    assert _live(tmp, path) == 1 and not path.exists()
    # A README edit or a Finder file under the hashed dirs is not hashed: no refusal.
    state["hashed_status"] = " M evals/README.md\n?? apps/api/app/prompts/.DS_Store\n"
    assert _live(tmp, path) == 0 and path.exists()


def test_clean_hashed_tree_writes_model_latest(fake):  # noqa: F811
    _, state, tmp = fake
    state["hashed_status"] = ""
    path = tmp / "model-latest.json"
    assert _live(tmp, path) == 0 and path.exists()


def test_unclean_hashed_paths_with_real_git(tmp_path):
    import subprocess

    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)

    _tree(tmp_path, {"evals/promptfooconfig.yaml": "tests: []\n",
                     "apps/api/app/prompts/chat/v6.md": "be safe\n",
                     "apps/api/app/tools.py": "T = 1\n"})
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    assert sc.unclean_hashed_paths(tmp_path) == []
    _tree(tmp_path, {"apps/api/app/prompts/.DS_Store": "finder", "evals/README.md": "n\n"})
    assert sc.unclean_hashed_paths(tmp_path) == []
    _tree(tmp_path, {"evals/scratch.yaml": "tests: [1]\n"})
    assert sc.unclean_hashed_paths(tmp_path) == ["evals/scratch.yaml"]
    (tmp_path / "evals/scratch.yaml").unlink()
    (tmp_path / "apps/api/app/tools.py").write_text("T = 2\n")
    assert sc.unclean_hashed_paths(tmp_path) == ["apps/api/app/tools.py"]


def test_edit_during_live_run_refuses_to_write(fake, monkeypatch):  # noqa: F811
    _, _, tmp = fake
    real = sc.current_fingerprint
    calls = {"n": 0}

    def moving():
        calls["n"] += 1
        fp = real()
        if calls["n"] > 1:  # the tree changed while the suites ran
            fp["evals_hash"] = "f" * 64
        return fp

    monkeypatch.setattr(sc, "current_fingerprint", moving)
    path = tmp / "model-latest.json"
    assert _live(tmp, path) == 1 and not path.exists()
    js = json.loads((tmp / "live" / "scorecard.json").read_text())
    assert js["verdict"] == "FAIL" and "evals_hash" in js["meta"]["model_evidence"]
    assert calls["n"] >= 2


def test_live_not_all_pass_warns_do_not_commit(fake, capsys):  # noqa: F811
    _, state, tmp = fake
    state["fail"] = {"eval": "timeout"}  # model rows not run: FAIL
    path = tmp / "model-latest.json"
    _live(tmp, path)
    err = capsys.readouterr().err
    assert "not all PASS" in err and "CI will reject it" in err and "do not commit" in err


def test_live_changed_known_residual_warns_do_not_commit(fake, capsys, monkeypatch):  # noqa: F811
    """A CHANGED watch row is not a FAIL locally, but CI rejects it: say so loudly."""
    _, _, tmp = fake
    real = sc.parse_promptfoo

    def residual_moved(path, category):
        cases = real(path, category)
        if category == "known_residuals" and cases:
            c = cases[0]
            cases[0] = sc.CaseResult(c.key, c.attempts, c.passes - 1, 0, c.failures + 1)
        return cases

    monkeypatch.setattr(sc, "parse_promptfoo", residual_moved)
    path = tmp / "model-latest.json"
    code = _live(tmp, path)
    data = json.loads(path.read_text())
    assert next(r for r in data["rows"] if r["key"] == "known_residuals")["verdict"] == "CHANGED"
    assert code == 0  # locally CHANGED does not fail the verdict
    assert "not all PASS" in capsys.readouterr().err
    _, _, js = _committed(tmp, path)
    assert _rows(js)["known_residuals"]["verdict"] == "FAIL"


def test_live_fail_verdict_warns_even_when_model_rows_pass(fake, capsys):  # noqa: F811
    _, state, tmp = fake
    state["fail"] = {"lint": 1}  # a deterministic row fails; model rows still PASS
    path = tmp / "model-latest.json"
    assert _live(tmp, path) == 1
    err = capsys.readouterr().err
    assert "verdict is FAIL" in err and "do not commit" in err


@pytest.mark.parametrize("bad", [
    {"passes": 11},                       # passes > attempts
    {"passes": -1},                       # negative
    {"passes": 9},                        # outcomes do not add up to attempts
])
def test_committed_impossible_case_counts_are_malformed(fake, evidence, bad):  # noqa: F811
    _, _, tmp = fake

    def edit(d):
        row = next(r for r in d["rows"] if r["key"] == "tool_injection")
        row["cases"][0].update(bad)

    _edit(evidence, edit)
    code, _, js = _committed(tmp, evidence)
    assert code == 1
    _assert_model_rows_fail(js, "MALFORMED")


def test_dependency_versions_recorded_but_not_fingerprinted(fake, evidence):  # noqa: F811
    _, _, tmp = fake
    head = json.loads(evidence.read_text())["header"]
    assert head["anthropic_version"] and head["pydantic_version"]
    assert "anthropic_version" not in sc.FINGERPRINT_FIELDS
    assert "pydantic_version" not in sc.FINGERPRINT_FIELDS
    _edit(evidence, lambda d: d["header"].update(anthropic_version="0.0.1",
                                                 pydantic_version="0.0.1"))
    code, _, js = _committed(tmp, evidence)
    assert code == 0 and js["verdict"] == "PASS"
