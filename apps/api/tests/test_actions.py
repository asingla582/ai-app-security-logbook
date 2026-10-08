"""Week 8: the approve path. What executes is the stored row, once, for its
requester only, and only while they are still a member."""

import os
import threading

import psycopg
import pytest

from app import tools as tools_mod
from app.gateway import FakeGateway, Reply, ToolProposal, get_gateway
from app.main import app

_DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


def _propose(client, args=None):
    args = args or {"title": "Approved", "body": "b"}
    app.dependency_overrides[get_gateway] = lambda: FakeGateway(
        script=[ToolProposal("create_note", args, 0, 0), Reply("waiting", 0, 0)]
    )
    try:
        client.post("/orgs", json={"name": "A"})
        conv = client.post("/conversations").json()["id"]
        pa = client.post(f"/conversations/{conv}/messages", json={"content": "save"}).json()["pending_action"]
    finally:
        app.dependency_overrides.pop(get_gateway, None)
    org_id = client.get("/orgs").json()[0]["id"]
    return pa, org_id, conv


def _titles(client, org_id):
    return [n["title"] for n in client.get(f"/orgs/{org_id}/notes").json()]


def test_approve_executes_the_stored_args(alice_client):
    pa, org_id, conv = _propose(alice_client)
    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "executed" and r.json()["note_id"]
    assert "Approved" in _titles(alice_client, org_id)
    with psycopg.connect(_DB_URL) as conn:
        assert conn.execute("select status from tool_calls where conversation_id = %s",
                            (conv,)).fetchone()[0] == "executed"
        assert conn.execute("select status, result_ref::text from pending_actions where id = %s",
                            (pa["id"],)).fetchone() == ("executed", r.json()["note_id"])


def test_second_approve_is_409_and_one_note(alice_client):
    pa, org_id, _ = _propose(alice_client)
    body = {"args_sha256": pa["args_sha256"]}
    assert alice_client.post(f"/actions/{pa['id']}/approve", json=body).status_code == 200
    assert alice_client.post(f"/actions/{pa['id']}/approve", json=body).status_code == 409
    assert _titles(alice_client, org_id).count("Approved") == 1


def test_concurrent_approves_create_one_note(alice_client):
    pa, org_id, _ = _propose(alice_client)
    codes = []

    def go():
        codes.append(alice_client.post(f"/actions/{pa['id']}/approve",
                                       json={"args_sha256": pa["args_sha256"]}).status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200, 409]
    assert _titles(alice_client, org_id).count("Approved") == 1


def test_altered_hash_is_409_and_nothing_runs(alice_client):
    pa, org_id, _ = _propose(alice_client)
    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": "f" * 64})
    assert r.status_code == 409
    assert "Approved" not in _titles(alice_client, org_id)


def test_client_supplied_args_are_rejected(alice_client):
    pa, _, _ = _propose(alice_client)
    r = alice_client.post(f"/actions/{pa['id']}/approve",
                          json={"args_sha256": pa["args_sha256"], "args": {"title": "evil"}})
    assert r.status_code == 422


def test_other_user_cannot_approve(alice_client, bob_setup):
    pa, org_id, _ = _propose(alice_client)
    r = bob_setup["client"].post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 404
    assert "Approved" not in _titles(alice_client, org_id)


def test_malformed_id_is_404(alice_client):
    r = alice_client.post("/actions/not-a-uuid/approve", json={"args_sha256": "x"})
    assert r.status_code == 404


def test_revoked_membership_blocks_approval(alice_client):
    pa, org_id, _ = _propose(alice_client)
    with psycopg.connect(_DB_URL) as conn:
        conn.execute("delete from memberships where org_id = %s", (org_id,))
        conn.commit()
    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 404
    with psycopg.connect(_DB_URL) as conn:
        assert conn.execute("select status from pending_actions where id = %s",
                            (pa["id"],)).fetchone()[0] == "pending"


def test_expired_action_is_410(alice_client):
    pa, org_id, _ = _propose(alice_client)
    # The trigger forbids changing expires_at, so seed a second, already-overdue row
    # against the same tool call as the superuser and approve that.
    with psycopg.connect(_DB_URL) as conn:
        row = conn.execute(
            "insert into pending_actions (tool_call_id, correlation_id, requester, org_id, "
            "tool_name, args, args_sha256, status, expires_at) "
            "select tool_call_id, correlation_id, requester, org_id, tool_name, args, "
            "args_sha256, 'pending', now() - interval '1 minute' from pending_actions "
            "where id = %s returning id::text",
            (pa["id"],),
        ).fetchone()
        conn.commit()
    r = alice_client.post(f"/actions/{row[0]}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 410


def test_deny_then_approve_is_409(alice_client):
    pa, org_id, _ = _propose(alice_client)
    assert alice_client.post(f"/actions/{pa['id']}/deny").json() == {"status": "denied"}
    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 409
    assert "Approved" not in _titles(alice_client, org_id)


def test_list_pending_for_conversation(alice_client, bob_setup):
    pa, _, conv = _propose(alice_client)
    mine = alice_client.get(f"/actions?status=pending&conversation_id={conv}").json()
    assert [a["id"] for a in mine] == [pa["id"]]
    assert mine[0]["args_sha256"] == pa["args_sha256"]
    assert bob_setup["client"].get(f"/actions?status=pending&conversation_id={conv}").json() == []


def test_stale_args_fail_closed_not_500(alice_client, monkeypatch):
    pa, org_id, _ = _propose(alice_client, {"title": "x" * 150, "body": ""})

    class Tighter(tools_mod.CreateNoteArgs):
        title: str = tools_mod.Field(max_length=10)

    spec = tools_mod.REGISTRY["create_note"]
    monkeypatch.setitem(tools_mod.REGISTRY, "create_note",
                        tools_mod.ToolSpec(**{**spec.__dict__, "args_model": Tighter}))
    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 200 and r.json() == {"status": "failed"}
    with psycopg.connect(_DB_URL) as conn:
        assert conn.execute("select status from pending_actions where id = %s",
                            (pa["id"],)).fetchone()[0] == "failed"


def test_unicode_args_round_trip_hash(alice_client):
    pa, org_id, _ = _propose(alice_client, {"title": "Café 🚀 会议", "body": "naïve"})
    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert "Café 🚀 会议" in _titles(alice_client, org_id)
