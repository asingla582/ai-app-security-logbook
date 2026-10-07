"""Week 8: an approval-required tool never executes in the proposal turn."""

import os

import psycopg

from app.gateway import FakeGateway, Reply, ToolProposal, get_gateway
from app.main import app
from app.prompting import ACTIVE_CHAT_VERSION, CHAT_TEMPLATE
from app.tools import REGISTRY

_DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


def _turn(client, proposal, reply="It is waiting for your approval."):
    app.dependency_overrides[get_gateway] = lambda: FakeGateway(script=[proposal, Reply(reply, 0, 0)])
    try:
        client.post("/orgs", json={"name": "A"})
        conv = client.post("/conversations").json()["id"]
        r = client.post(f"/conversations/{conv}/messages", json={"content": "save a note"})
    finally:
        app.dependency_overrides.pop(get_gateway, None)
    return conv, r


def test_registry_marks_only_create_note_for_approval():
    assert REGISTRY["create_note"].requires_approval is True
    assert REGISTRY["search_documents"].requires_approval is False


def test_template_v6_is_active_and_mentions_approval():
    assert ACTIVE_CHAT_VERSION == 6
    assert "approval" in CHAT_TEMPLATE.system


def test_create_note_is_parked_not_executed(alice_client):
    conv, r = _turn(alice_client, ToolProposal("create_note", {"title": "Held", "body": "b"}, 0, 0))
    assert r.status_code == 201
    body = r.json()
    assert body["tool_used"] is None
    pa = body["pending_action"]
    assert pa["tool_name"] == "create_note"
    assert pa["args"] == {"title": "Held", "body": "b"}
    assert len(pa["args_sha256"]) == 64
    assert set(pa["flags"]) == {"urls", "doc_spans"}
    org_id = alice_client.get("/orgs").json()[0]["id"]
    assert "Held" not in [n["title"] for n in alice_client.get(f"/orgs/{org_id}/notes").json()]
    with psycopg.connect(_DB_URL) as conn:
        tc = conn.execute("select status from tool_calls where conversation_id = %s", (conv,)).fetchall()
        pend = conn.execute("select status from pending_actions where conversation_id = %s", (conv,)).fetchall()
    assert tc == [("pending_approval",)]
    assert pend == [("pending",)]


def test_second_leg_sees_awaiting_approval_chunk(alice_client):
    seen = {}

    class Spy(FakeGateway):
        def propose(self, system, messages, tools):
            return ToolProposal("create_note", {"title": "Spy", "body": ""}, 0, 0)

        def complete(self, system, messages):
            seen["text"] = "\n".join(m["content"] for m in messages)
            return Reply("waiting", 0, 0)

    app.dependency_overrides[get_gateway] = lambda: Spy()
    try:
        alice_client.post("/orgs", json={"name": "A"})
        conv = alice_client.post("/conversations").json()["id"]
        alice_client.post(f"/conversations/{conv}/messages", json={"content": "save Spy"})
    finally:
        app.dependency_overrides.pop(get_gateway, None)
    assert "pending the user's approval" in seen["text"]
    assert "It has NOT been saved" in seen["text"]


def test_search_documents_still_executes_without_approval(alice_client):
    _, r = _turn(alice_client, ToolProposal("search_documents", {"query": "x"}, 0, 0), reply="none")
    assert r.json()["tool_used"]["name"] == "search_documents"
    assert r.json()["pending_action"] is None
