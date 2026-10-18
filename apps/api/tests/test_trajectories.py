"""Week 9: trajectory-level tests. Assert the whole ordered sequence of decisions
recorded in tool_calls and pending_actions for tool and approval flows, not just
the final reply. All reads are superuser reads straight from the database."""

import os

import psycopg

from app.gateway import FakeGateway, Reply, ToolProposal, get_gateway
from app.main import app

_DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


def trajectory(conversation_id: str) -> list[tuple[str, str]]:
    """(tool_name, status) from tool_calls for one conversation, in proposal order."""
    with psycopg.connect(_DB_URL) as conn:
        return conn.execute(
            "select tool_name, status from tool_calls where conversation_id = %s"
            " order by proposed_at, id",
            (conversation_id,),
        ).fetchall()


def _pending(conversation_id):
    with psycopg.connect(_DB_URL) as conn:
        return conn.execute(
            "select id::text, status, result_ref::text, args from pending_actions"
            " where conversation_id = %s order by created_at",
            (conversation_id,),
        ).fetchall()


def _notes(org_id):
    with psycopg.connect(_DB_URL) as conn:
        return conn.execute(
            "select id::text, title, body from notes where org_id = %s", (org_id,)
        ).fetchall()


def _turn(client, proposal, reply="done"):
    app.dependency_overrides[get_gateway] = lambda: FakeGateway(
        script=[proposal, Reply(reply, 0, 0)]
    )
    try:
        client.post("/orgs", json={"name": "A"})
        conv = client.post("/conversations").json()["id"]
        r = client.post(f"/conversations/{conv}/messages", json={"content": "go"})
    finally:
        app.dependency_overrides.pop(get_gateway, None)
    org_id = client.get("/orgs").json()[0]["id"]
    return conv, org_id, r


def _note_turn(client, args=None):
    args = args or {"title": "Traj", "body": "the body"}
    conv, org_id, r = _turn(client, ToolProposal("create_note", args, 0, 0))
    assert r.status_code == 201
    return conv, org_id, r.json()["pending_action"]


def test_read_tool_trajectory(alice_client):
    conv, _, r = _turn(alice_client, ToolProposal("search_documents", {"query": "x"}, 0, 0))
    assert r.status_code == 201
    assert trajectory(conv) == [("search_documents", "executed")]
    assert _pending(conv) == []


def test_write_approved_trajectory(alice_client):
    conv, org_id, pa = _note_turn(alice_client)
    assert trajectory(conv) == [("create_note", "pending_approval")]
    assert [p[1] for p in _pending(conv)] == ["pending"]
    assert _notes(org_id) == []

    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "executed"
    note_id = r.json()["note_id"]

    assert trajectory(conv) == [("create_note", "executed")]
    (pid, status, result_ref, args), = _pending(conv)
    assert status == "executed"
    assert result_ref == note_id
    notes = _notes(org_id)
    assert len(notes) == 1
    assert notes[0] == (note_id, args["title"], args["body"])
    assert (notes[0][1], notes[0][2]) == (pa["args"]["title"], pa["args"]["body"])


def test_write_denied_trajectory(alice_client):
    conv, org_id, pa = _note_turn(alice_client)
    assert alice_client.post(f"/actions/{pa['id']}/deny").json() == {"status": "denied"}
    assert trajectory(conv) == [("create_note", "denied")]
    assert [p[1] for p in _pending(conv)] == ["denied"]
    assert _notes(org_id) == []


def test_write_expired_trajectory(alice_client):
    conv, org_id, pa = _note_turn(alice_client)
    # The trigger forbids changing expires_at or deleting, so seed a second,
    # already-overdue row against the same tool call as the superuser, as
    # test_actions.py does, and approve that one.
    with psycopg.connect(_DB_URL) as conn:
        row = conn.execute(
            "insert into pending_actions (tool_call_id, correlation_id, requester, org_id, "
            "tool_name, args, args_sha256, status, expires_at, conversation_id) "
            "select tool_call_id, correlation_id, requester, org_id, tool_name, args, "
            "args_sha256, 'pending', now() - interval '1 minute', conversation_id "
            "from pending_actions where id = %s returning id::text",
            (pa["id"],),
        ).fetchone()
        conn.commit()
    r = alice_client.post(f"/actions/{row[0]}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 410
    assert trajectory(conv) == [("create_note", "expired")]
    by_id = {p[0]: p[1] for p in _pending(conv)}
    assert by_id[row[0]] == "expired"
    assert _notes(org_id) == []


def test_injected_org_id_trajectory(alice_client):
    conv, org_id, r = _turn(
        alice_client,
        ToolProposal(
            "create_note",
            {"title": "t", "body": "b", "org_id": "00000000-0000-0000-0000-000000000000"},
            0,
            0,
        ),
    )
    assert r.status_code == 201
    assert r.json()["pending_action"] is None
    assert trajectory(conv) == [("create_note", "invalid")]
    assert _pending(conv) == []
    assert _notes(org_id) == []


def test_wrong_hash_then_right_hash(alice_client):
    conv, org_id, pa = _note_turn(alice_client)
    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": "f" * 64})
    assert r.status_code == 409
    assert [p[1] for p in _pending(conv)] == ["pending"]
    assert _notes(org_id) == []

    r = alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert trajectory(conv) == [("create_note", "executed")]
    assert [p[1] for p in _pending(conv)] == ["executed"]
    assert len(_notes(org_id)) == 1


def test_no_executed_write_without_executed_approval(alice_client):
    # Drive one approved write so the sweep has something to check, then sweep the DB.
    conv, _, pa = _note_turn(alice_client)
    alice_client.post(f"/actions/{pa['id']}/approve", json={"args_sha256": pa["args_sha256"]})
    with psycopg.connect(_DB_URL) as conn:
        orphans = conn.execute(
            "select t.id from tool_calls t where t.tool_name = 'create_note'"
            " and t.status = 'executed' and not exists ("
            "  select 1 from pending_actions p where p.tool_call_id = t.id"
            "  and p.status = 'executed')"
        ).fetchall()
    assert orphans == []
