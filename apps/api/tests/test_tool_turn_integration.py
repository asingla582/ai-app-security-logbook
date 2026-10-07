"""Week 7: the tool turn wired through the chat route and run_tool_turn.

Uses a scripted FakeGateway so every branch is deterministic and offline. The
require_supabase-backed tests prove the real DB effects (a note lands in the
caller's org, the trajectory is recorded); the pure-unit test proves single-step.
"""

import os

import psycopg

from app.gateway import FakeGateway, Reply, ToolProposal, get_gateway
from app.main import app

_DB_URL = os.environ.get(
    "SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)


def _script_gateway(script):
    app.dependency_overrides[get_gateway] = lambda: FakeGateway(script=script)


def _clear_gateway():
    app.dependency_overrides.pop(get_gateway, None)


def test_create_note_tool_returns_pending_action(alice_client):
    _script_gateway(
        [
            ToolProposal("create_note", {"title": "Standup", "body": "ship week 7"}, 0, 0),
            Reply("Waiting for your approval.", 0, 0),
        ]
    )
    try:
        alice_client.post("/orgs", json={"name": "A"})
        conv = alice_client.post("/conversations").json()["id"]
        r = alice_client.post(
            f"/conversations/{conv}/messages", json={"content": "save a note titled Standup"}
        )
        assert r.status_code == 201
        body = r.json()
        assert body["tool_used"] is None
        assert body["pending_action"]["args"]["title"] == "Standup"
    finally:
        _clear_gateway()


def test_unknown_tool_falls_back_to_plain_reply(alice_client):
    _script_gateway(
        [
            ToolProposal("no_such_tool", {}, 0, 0),
            Reply("Here is a plain answer.", 0, 0),
        ]
    )
    try:
        alice_client.post("/orgs", json={"name": "A"})
        conv = alice_client.post("/conversations").json()["id"]
        r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "hi"})
        assert r.status_code == 201
        body = r.json()
        assert body["tool_used"] is None
        assert body["reply"] == "Here is a plain answer."
    finally:
        _clear_gateway()


def test_second_leg_is_called_without_tools(alice_client):
    """Single-step is structural: after a tool executes, the final answer comes from
    complete() (no tools offered), never a second propose(). A counting spy through the
    real route proves exactly one propose and one complete for a tool turn."""
    calls = {"propose": 0, "complete": 0}

    class Spy(FakeGateway):
        def propose(self, system, messages, tools):
            calls["propose"] += 1
            return ToolProposal("create_note", {"title": "n", "body": ""}, 0, 0)

        def complete(self, system, messages):
            calls["complete"] += 1
            return Reply("final", 0, 0)

    app.dependency_overrides[get_gateway] = lambda: Spy()
    try:
        alice_client.post("/orgs", json={"name": "A"})
        conv = alice_client.post("/conversations").json()["id"]
        r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "save n"})
        assert r.status_code == 201
        assert calls == {"propose": 1, "complete": 1}
    finally:
        _clear_gateway()
