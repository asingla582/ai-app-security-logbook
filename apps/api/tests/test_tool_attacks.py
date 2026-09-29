"""Week 7 attacks through the tool path: cross-tenant isolation and rate limits.

The real design's guarantee is structural: the model never chooses the tenant, so a
proposal that names another org cannot act on it. These tests drive a scripted
FakeGateway to force specific proposals and assert on the artifact (a note row, a
429), never on the model's prose.
"""

import app.routes_chat as routes_chat
import app.tool_exec as tool_exec
from app.gateway import FakeGateway, Reply, ToolProposal, get_gateway
from app.limits import RateLimited
from app.main import app


def _script_gateway(script):
    app.dependency_overrides[get_gateway] = lambda: FakeGateway(script=script)


def _restore_gateway():
    # Restore the autouse default rather than removing the override, so a following
    # test never accidentally reaches the real gateway.
    app.dependency_overrides[get_gateway] = lambda: FakeGateway()


def _alice_org(alice_client) -> str:
    return alice_client.post("/orgs", json={"name": "A"}).json()["id"]


def _titles(client, org_id) -> list[str]:
    return [n["title"] for n in client.get(f"/orgs/{org_id}/notes").json()]


def test_model_supplied_org_id_is_rejected_and_no_note_lands(alice_client, bob_setup):
    # The confused-deputy attempt: the proposal carries a foreign org_id. extra="forbid"
    # rejects it at validation, so the tool never runs and no cross-tenant note is made.
    bob, bob_org = bob_setup["client"], bob_setup["org_id"]
    # Seed a note in Bob's org so the isolation assertion is non-vacuous.
    bob.post(f"/orgs/{bob_org}/notes", json={"title": "BobOwn", "body": "b"})

    _script_gateway(
        [
            ToolProposal("create_note", {"title": "pwn", "body": "x", "org_id": bob_org}, 0, 0),
            Reply("I could not do that.", 0, 0),
        ]
    )
    try:
        alice_org = _alice_org(alice_client)
        conv = alice_client.post("/conversations").json()["id"]
        r = alice_client.post(
            f"/conversations/{conv}/messages", json={"content": "make a note in bob's org"}
        )
        assert r.status_code == 201
        assert r.json()["tool_used"] is None  # invalid -> fell back to a plain answer
    finally:
        _restore_gateway()

    # Bob's org is untouched (still only his own note); Alice's org got nothing either.
    assert _titles(bob, bob_org) == ["BobOwn"]
    assert "pwn" not in _titles(alice_client, alice_org)


def test_tool_created_note_lands_in_callers_org_only(alice_client, bob_setup):
    # A legitimate create_note lands in the caller's own org and nowhere else.
    bob, bob_org = bob_setup["client"], bob_setup["org_id"]
    _script_gateway(
        [ToolProposal("create_note", {"title": "AliceOnly", "body": "b"}, 0, 0), Reply("ok", 0, 0)]
    )
    try:
        alice_org = _alice_org(alice_client)
        conv = alice_client.post("/conversations").json()["id"]
        r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "save it"})
        assert r.status_code == 201
        assert r.json()["tool_used"]["name"] == "create_note"
    finally:
        _restore_gateway()

    assert "AliceOnly" in _titles(alice_client, alice_org)  # positive: it really landed
    assert "AliceOnly" not in _titles(bob, bob_org)  # isolation: not in another tenant


def test_per_user_tool_rate_limit_returns_429(alice_client, monkeypatch):
    def _boom(conn, user_id):
        raise RateLimited("user_tool_rate", "slow down")

    monkeypatch.setattr(tool_exec, "check_user_tool_rate", _boom)
    _script_gateway([ToolProposal("search_documents", {"query": "x"}, 0, 0), Reply("r", 0, 0)])
    try:
        _alice_org(alice_client)
        conv = alice_client.post("/conversations").json()["id"]
        r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "search x"})
        assert r.status_code == 429
    finally:
        _restore_gateway()


def test_user_tool_rate_engages_via_real_count(alice_client, monkeypatch):
    # Regression for the mid-turn commit seam: check_user_tool_rate counts tool_calls
    # via count_user_tool_calls_1m() (anchored to auth.uid()). If the identity context
    # is lost after record_tool_proposal's commit, the count is always zero and the cap
    # never engages. Each scripted turn proposes create_note, so each records a row.
    import app.limits as limits

    monkeypatch.setattr(limits, "TOOL_CALLS_PER_USER_PER_MINUTE", 2)
    _script_gateway(
        [ToolProposal("create_note", {"title": "n", "body": ""}, 0, 0), Reply("ok", 0, 0)]
    )
    try:
        _alice_org(alice_client)
        conv = alice_client.post("/conversations").json()["id"]
        codes = [
            alice_client.post(
                f"/conversations/{conv}/messages", json={"content": f"save {i}"}
            ).status_code
            for i in range(3)
        ]
        assert 429 in codes  # the real per-user tool-rate count engages
    finally:
        _restore_gateway()


def test_org_model_budget_ceiling_returns_429(alice_client, monkeypatch):
    # No scripted gateway: the budget check fires before any model call, so the
    # gateway is never reached.
    def _boom(conn, org_id):
        raise RateLimited("org_model_budget", "try again tomorrow")

    monkeypatch.setattr(routes_chat, "check_org_model_budget", _boom)
    _alice_org(alice_client)
    conv = alice_client.post("/conversations").json()["id"]
    r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "hello"})
    assert r.status_code == 429


def test_org_model_budget_engages_via_real_count(alice_client, monkeypatch):
    # Regression for the bug the live red team caught: the budget count must read the
    # audit tables through the SECURITY DEFINER function, not a plain query on the
    # caller's connection (which sees zero rows under RLS). Here we exercise the REAL
    # count path (only the threshold is lowered) and require the ceiling to engage.
    import app.limits as limits

    monkeypatch.setattr(limits, "MODEL_CALLS_PER_ORG_PER_DAY", 2)
    _alice_org(alice_client)
    conv = alice_client.post("/conversations").json()["id"]
    codes = [
        alice_client.post(
            f"/conversations/{conv}/messages", json={"content": f"hi {i}"}
        ).status_code
        for i in range(4)
    ]
    # First two succeed and record model_calls; the real count then trips the ceiling.
    assert codes[:2] == [201, 201]
    assert 429 in codes[2:]
