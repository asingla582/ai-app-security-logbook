import json

import scorecard as sc
from scorecard_config import CATEGORIES, FILE_MAP, NAME_OVERRIDES

FIX = __import__("pathlib").Path(__file__).parent / "fixtures"
CATS = {c.key: c for c in CATEGORIES}
MODEL_KEYS = ["direct_injection", "indirect_injection", "tool_injection"]


def T(file, name, outcome="passed"):
    return sc.TestResult(file, name, outcome)


def C(key, attempts, passes, errors=0):
    return sc.CaseResult(key, attempts, passes, errors)


def full_buckets():
    """Every required category has one passing result."""
    b = {}
    for c in CATEGORIES:
        if c.kind in ("model", "watch"):
            b[c.key] = [C(c.key + "|{}", 3, 3)]
        else:
            b[c.key] = [T("f.py", "t_" + c.key)]
    return b


def run(buckets, event="push", flags=None, **kw):
    # file_map={} switches off the per-file presence rule; the C2 tests turn it on.
    kw.setdefault("file_map", {})
    kw.setdefault("repeat", 3)
    return sc.score(CATEGORIES, buckets, flags or {}, event, **kw)


def real_buckets():
    """One passing test per real FILE_MAP file, plus passing model cases."""
    b = {}
    for path, key in FILE_MAP.items():
        b.setdefault(key, []).append(T(path, "t"))
    for k in MODEL_KEYS + ["known_residuals"]:
        b.setdefault(k, []).append(C(k + "|{}", 3, 3))
    return b


def row(card, key):
    return next(r for r in card.rows if r.key == key)


def test_full_green_baseline_passes():
    assert run(full_buckets()).verdict == "PASS"


def test_every_collected_test_maps_to_exactly_one_category():
    results = [
        T("apps/api/tests/test_prompting.py", "test_a"),
        T("apps/api/tests/test_brand_new.py", "test_b"),  # unmapped
        T("apps/api/tests/test_redaction.py", "test_residual_x"),  # name override wins
    ]
    buckets = sc.categorize(results, FILE_MAP, NAME_OVERRIDES)
    assert sum(len(v) for v in buckets.values()) == len(results)
    assert [r.name for r in buckets["structural"]] == ["test_a"]
    assert [r.name for r in buckets["other_deterministic"]] == ["test_b"]
    assert [r.name for r in buckets["known_residuals"]] == ["test_residual_x"]


def test_junit_maps_by_classname_when_file_is_absent():
    res = sc.parse_junit(FIX / "junit_gate_skipped.xml")
    b = sc.categorize(res)
    assert len(b["approval_gate"]) == 3
    assert len(b["structural"]) == 1
    assert [r.name for r in b["known_residuals"]] == ["test_residual_obfuscated_email_slips_through"]


def test_skipped_required_tests_fail_the_category():
    b = full_buckets()
    b["approval_gate"] = sc.categorize(sc.parse_junit(FIX / "junit_gate_skipped.xml"))["approval_gate"]
    card = run(b)
    r = row(card, "approval_gate")
    assert r.ran is False
    assert r.verdict == "FAIL"
    assert card.verdict == "FAIL"


def test_zero_results_is_not_run_and_fails():
    b = full_buckets()
    del b["trajectory"]
    card = run(b)
    assert row(card, "trajectory").ran is False
    assert card.verdict == "FAIL"


def test_ran_flag_false_overrides_results():
    card = run(full_buckets(), flags={"platform_hygiene": False})
    assert row(card, "platform_hygiene").ran is False
    assert card.verdict == "FAIL"


def test_vitest_statuses():
    res = sc.parse_vitest_json(FIX / "vitest.json")
    assert [r.outcome for r in res] == ["passed", "skipped", "failed"]
    b = sc.categorize(res)
    assert len(b["output_handling"]) == 2
    assert len(b["other_deterministic"]) == 1


def test_promptfoo_error_result_fails_case(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"results": {"results": [
        {"testCase": {"description": "d"}, "vars": {"a": 1}, "success": True, "error": "boom"},
        {"testCase": {"description": "d"}, "vars": {"a": 1}, "success": True, "error": None},
    ]}}))
    (case,) = sc.parse_promptfoo(p, "direct_injection")
    assert (case.attempts, case.passes, case.errors) == (2, 1, 1)
    assert not case.passed


def test_repeat_grouping_keys_on_description_and_vars(tmp_path):
    items = [
        {"testCase": {"description": "same"}, "vars": {"attack": v}, "success": True, "error": None}
        for v in ("one", "two") for _ in range(3)
    ]
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"results": {"results": items}}))
    cases = sc.parse_promptfoo(p, "direct_injection")
    assert len(cases) == 2
    assert all(c.attempts == 3 for c in cases)
    assert cases[0].key != cases[1].key


def test_residual_prefix_routes_out_of_direct_injection():
    direct = sc.parse_promptfoo(FIX / "promptfoo.json", "direct_injection")
    resid = sc.parse_promptfoo(FIX / "promptfoo.json", "known_residuals")
    assert [c.attempts for c in direct] == [2]
    assert len(resid) == 1 and resid[0].key.startswith("KNOWN RESIDUAL")


def test_model_case_passes_only_if_all_attempts_pass():
    b = full_buckets()
    b["direct_injection"] = [C("a|{}", 3, 3), C("b|{}", 3, 2)]
    card = run(b)
    r = row(card, "direct_injection")
    assert (r.passed, r.total, r.runs, r.attempts) == (1, 2, 3, 6)
    assert r.verdict == "FAIL" and card.verdict == "FAIL"


def test_model_row_not_run_when_only_deterministic_tests_exist():
    b = full_buckets()
    b["indirect_injection"] = [T("apps/api/tests/test_provenance.py", "test_x")]
    card = run(b)
    assert row(card, "indirect_injection").ran is False
    assert card.verdict == "FAIL"


def test_watch_row_changes_do_not_fail_verdict():
    b = full_buckets()
    b["known_residuals"] = [C("KNOWN RESIDUAL x|{}", 3, 2)]
    card = run(b)
    assert row(card, "known_residuals").verdict == "CHANGED"
    assert card.verdict == "PASS"


def test_watch_row_not_run_still_fails():
    b = full_buckets()
    del b["known_residuals"]
    assert run(b).verdict == "FAIL"


def test_fork_pr_model_rows_not_required():
    b = full_buckets()
    for k in MODEL_KEYS + ["known_residuals"]:
        del b[k]
    card = run(b, event="fork_pr")
    assert card.verdict == "PASS (partial: model suites not run on fork PR)"
    assert all(row(card, k).ran is False for k in MODEL_KEYS)


def test_fork_pr_still_fails_when_deterministic_row_missing():
    b = full_buckets()
    for k in MODEL_KEYS + ["known_residuals", "approval_gate"]:
        del b[k]
    assert run(b, event="fork_pr").verdict == "FAIL"


def test_same_repo_pr_requires_model_rows():
    b = full_buckets()
    del b["direct_injection"]
    assert run(b, event="pull_request").verdict == "FAIL"
    assert run(b, event="push").verdict == "FAIL"


def test_fork_pr_model_row_that_ran_and_failed_still_fails():
    b = full_buckets()
    b["direct_injection"] = [C("a|{}", 3, 2)]
    assert run(b, event="fork_pr").verdict == "FAIL"


def test_deterministic_threshold_is_100_percent():
    b = full_buckets()
    b["cross_tenant"] = [T("f", f"t{i}") for i in range(99)] + [T("f", "bad", "failed")]
    card = run(b)
    assert row(card, "cross_tenant").verdict == "FAIL"
    assert card.verdict == "FAIL"


def test_error_outcome_fails_and_partial_skip_fails():
    b = full_buckets()
    b["trajectory"] = [T("f", "a"), T("f", "b", "skipped")]
    r = row(run(b), "trajectory")
    assert r.ran and r.verdict == "FAIL"


def test_json_and_md_carry_ran_and_counts():
    card = run(full_buckets())
    data = sc.render_json(card)
    assert data["verdict"] == "PASS"
    assert len(data["rows"]) == len(CATEGORIES)
    for r in data["rows"]:
        for k in ("ran", "runs", "passed", "total", "threshold", "verdict"):
            assert k in r
    md = sc.render_md(card)
    for c in CATEGORIES:
        line = next(ln for ln in md.splitlines() if ln.startswith(f"| {c.title} "))
        assert c.threshold in line and "PASS" in line
    assert "—" not in md


def test_md_marks_not_run_visibly():
    b = full_buckets()
    del b["trajectory"]
    md = sc.render_md(run(b))
    assert "NOT RUN" in md
    line = next(ln for ln in md.splitlines() if ln.startswith("| Trajectory "))
    assert "| NO |" in line and "FAIL" in line


def test_main_exit_codes(tmp_path):
    jx = tmp_path / "j.xml"
    jx.write_text('<testsuites><testsuite><testcase classname="tests.test_prompting" name="t"/></testsuite></testsuites>')
    out = tmp_path / "out"
    # Only one category ran: everything else is not run, so the exit is non-zero.
    assert sc.main(["--junit", str(jx), "--event", "push", "--out", str(out)]) == 1
    assert json.loads((out / "scorecard.json").read_text())["verdict"] == "FAIL"
    assert (out / "scorecard.md").exists()


def test_detect_event(tmp_path):
    assert sc.detect_event({}) == "local"
    assert sc.detect_event({"GITHUB_EVENT_NAME": "push"}) == "push"
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"pull_request": {"head": {"repo": {"full_name": "x/r"}}, "base": {"repo": {"full_name": "o/r"}}}}))
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(ev)}
    assert sc.detect_event(env) == "fork_pr"
    ev.write_text(json.dumps({"pull_request": {"head": {"repo": {"full_name": "o/r"}}, "base": {"repo": {"full_name": "o/r"}}}}))
    assert sc.detect_event(env) == "pull_request"


# ---- fix round 1: regression tests for the false-PASS paths


def test_c1_fork_pr_deterministic_failures_in_model_row_still_fail():
    # test_provenance / test_rag_chat sit in indirect_injection. All failing on a fork PR
    # used to read PASS (partial) with the row "NOT RUN 0/11".
    b = full_buckets()
    for k in MODEL_KEYS + ["known_residuals"]:
        del b[k]
    b["indirect_injection"] = [T("apps/api/tests/test_provenance.py", f"t{i}", "failed") for i in range(11)]
    card = run(b, event="fork_pr")
    assert card.verdict == "FAIL"
    assert row(card, "indirect_injection").verdict == "FAIL"


def test_c1_fork_pr_skipped_deterministic_tests_in_model_row_fail():
    b = full_buckets()
    for k in MODEL_KEYS + ["known_residuals"]:
        del b[k]
    b["indirect_injection"] = [T("apps/api/tests/test_rag_chat.py", "t", "skipped")]
    assert run(b, event="fork_pr").verdict == "FAIL"


def test_c1_fork_pr_residual_pytest_pins_still_required():
    b = full_buckets()
    for k in MODEL_KEYS:
        del b[k]
    b["known_residuals"] = [T("apps/api/tests/test_redaction.py", "test_residual_x", "skipped")]
    assert run(b, event="fork_pr").verdict == "FAIL"
    b["known_residuals"] = [T("apps/api/tests/test_redaction.py", "test_residual_x")]
    assert run(b, event="fork_pr").verdict == PARTIAL


PARTIAL = "PASS (partial: model suites not run on fork PR)"


def test_c2_real_baseline_is_green_and_missing_vitest_fails():
    b = real_buckets()
    assert run(b, file_map=FILE_MAP).verdict == "PASS"
    web = {p for p in FILE_MAP if p.startswith("apps/web/")}
    for k, items in b.items():
        b[k] = [i for i in items if not (isinstance(i, sc.TestResult) and i.file in web)]
    card = run(b, file_map=FILE_MAP)
    assert card.verdict == "FAIL"
    assert any("MISSING: apps/web/src/lib/actions.test.ts" in f for r in card.rows for f in r.failures)
    assert "MISSING: apps/web/src/lib/actions.test.ts" in sc.render_md(card)


def test_c2_dropping_a_files_results_fails_its_row():
    b = real_buckets()
    b["approval_gate"] = [i for i in b["approval_gate"] if i.file != "apps/api/tests/test_approval_gate.py"]
    card = run(b, file_map=FILE_MAP)
    assert card.verdict == "FAIL"
    assert row(card, "approval_gate").verdict == "FAIL"


def test_c2_a_files_results_all_skipped_counts_as_missing():
    b = real_buckets()
    b["trajectory"] = [T("apps/api/tests/test_trajectories.py", "t", "skipped")]
    assert run(b, file_map=FILE_MAP).verdict == "FAIL"


def test_c2_vitest_suite_that_fails_to_import_is_a_failure(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"success": False, "numFailedTestSuites": 1, "testResults": [
        {"name": "/r/apps/web/src/lib/api.test.ts", "status": "failed", "assertionResults": []}]}))
    res = sc.parse_vitest_json(p)
    assert [r.outcome for r in res] == ["error"]
    assert row(run({"other_deterministic": res}), "other_deterministic").verdict == "FAIL"


def test_c2_vitest_top_level_failure_with_no_failing_tests(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"success": False, "testResults": [
        {"name": "/r/a.test.ts", "status": "passed",
         "assertionResults": [{"title": "t", "status": "passed"}]}]}))
    assert "error" in [r.outcome for r in sc.parse_vitest_json(p)]


def test_c2_unreadable_source_fails_main(tmp_path):
    assert sc.main(["--junit", str(tmp_path / "nope.xml"), "--event", "push"]) == 1


def test_c2_main_without_vitest_is_not_green(tmp_path):
    jx = tmp_path / "j.xml"
    cases = "".join(f'<testcase classname="x" file="{p}" name="t"/>' for p, _ in FILE_MAP.items() if not p.startswith("apps/web/"))
    jx.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    assert sc.main(["--junit", str(jx), "--event", "fork_pr"]) == 1


def test_i3_case_with_fewer_attempts_than_repeat_fails():
    b = full_buckets()
    b["direct_injection"] = [C("a|{}", 1, 1)]
    card = run(b, repeat=3)
    assert row(card, "direct_injection").verdict == "FAIL" and card.verdict == "FAIL"


def test_i3_partial_promptfoo_run_fails_on_expected_case_count():
    b = full_buckets()
    b["direct_injection"] = [C("a|{}", 3, 3)]
    assert run(b, expected_cases={"direct_injection": 20}).verdict == "FAIL"
    assert run(b, expected_cases={"direct_injection": 1}).verdict == "PASS"


def test_i3_cases_without_repeat_n_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        sc.score(CATEGORIES, full_buckets(), {}, "push", file_map={}, repeat=None)


def test_i4_all_error_residual_case_is_not_run_and_fails():
    b = full_buckets()
    b["known_residuals"] = [C("KNOWN RESIDUAL x|{}", 3, 0, errors=3)]
    card = run(b)
    assert row(card, "known_residuals").verdict == "FAIL"
    assert card.verdict == "FAIL"


def test_i5_residual_override_is_scoped_to_its_file():
    res = [
        T("tests/scorecard/test_scorecard.py", "test_residual_prefix_routes_out_of_direct_injection", "failed"),
        T("apps/api/tests/test_redaction.py", "test_residual_a"),
    ]
    b = sc.categorize(res)
    assert [r.name for r in b["other_deterministic"]] == ["test_residual_prefix_routes_out_of_direct_injection"]
    assert [r.name for r in b["known_residuals"]] == ["test_residual_a"]


def test_minor_sources_in_md_and_json_and_junit_prefers_file_attr(tmp_path):
    card = run(real_buckets(), file_map=FILE_MAP)
    d = sc.render_json(card)
    direct = next(r for r in d["rows"] if r["key"] == "direct_injection")
    assert "evals/promptfooconfig.yaml" in direct["sources"]
    assert "| Sources |" in sc.render_md(card)
    jx = tmp_path / "j.xml"
    jx.write_text('<testsuites><testcase classname="tests.test_other" file="apps/api/tests/test_actions.py" name="t"/></testsuites>')
    assert sc.parse_junit(jx)[0].file == "apps/api/tests/test_actions.py"


def test_categories_are_the_spec_rows():
    assert set(CATS) == {
        "structural", "cross_tenant", "approval_gate", "output_handling", "trajectory",
        "tool_abuse_limits", "direct_injection", "indirect_injection", "tool_injection",
        "known_residuals", "platform_hygiene", "other_deterministic",
    }
    assert all(c.required for c in CATEGORIES)
    assert set(FILE_MAP.values()) <= set(CATS)
