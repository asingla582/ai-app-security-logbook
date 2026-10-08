"""Week 8 final review: the output allowlist comes only from document-derived chunks.

The Week 6 grant is what a DOCUMENT literally says, never what the model assembles.
App-generated chunks (the pending-approval note, the note-confirmation) echo
model-written text back into context, so a URL the model put in a proposed title
must not become clickable in the reply. These drive the real route with a scripted
FakeGateway and assert on the stored reply.
"""

import os

import psycopg

import app.routes_chat as routes_chat
from app.gateway import FakeGateway, Reply, ToolProposal, get_gateway
from app.main import app

_DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")

EVIL = "https://evil.example/login?u=alice"
DOC_URL = "https://docs.example.com/handbook"


def _post(client, script, content="save a note", doc=None):
    org = client.post("/orgs", json={"name": "A"}).json()["id"]
    if doc is not None:
        client.post(f"/orgs/{org}/documents", json={"filename": "handbook.md", "content": doc})
    conv = client.post("/conversations").json()["id"]
    app.dependency_overrides[get_gateway] = lambda: FakeGateway(script=script)
    try:
        r = client.post(f"/conversations/{conv}/messages", json={"content": content})
    finally:
        app.dependency_overrides[get_gateway] = lambda: FakeGateway()
    assert r.status_code == 201, r.text
    return conv, r.json()


def _stored_reply(conv) -> str:
    with psycopg.connect(_DB_URL) as conn:
        row = conn.execute(
            "select content from messages where conversation_id = %s and role = 'assistant'",
            (conv,),
        ).fetchone()
    return row[0]


def test_url_in_proposed_title_is_not_granted_by_the_pending_chunk(alice_client):
    conv, body = _post(
        alice_client,
        [ToolProposal("create_note", {"title": f"see {EVIL}", "body": "b"}, 0, 0),
         Reply(f"Saved for approval. [sign in]({EVIL})", 0, 0)],
    )
    assert body["pending_action"] is not None
    for text in (body["reply"], _stored_reply(conv)):
        assert f"]({EVIL})" not in text
        assert "hxxps://evil[.]example/login?u=alice" in text


def test_url_in_a_retrieved_document_still_stays_live(alice_client):
    doc = f"The handbook lives at {DOC_URL} for everyone. " * 5
    conv, body = _post(
        alice_client,
        [Reply(f"See [the handbook]({DOC_URL}).", 0, 0)],
        content="where is the handbook?",
        doc=doc,
    )
    assert body["sources"], "the seeded document should be retrieved"
    assert f"[the handbook]({DOC_URL})" in body["reply"]
    assert f"[the handbook]({DOC_URL})" in _stored_reply(conv)


def test_url_in_a_search_documents_result_still_stays_live(alice_client, monkeypatch):
    # Empty chat retrieval, so the only source of the URL is the tool's result.
    monkeypatch.setattr(routes_chat, "retrieve_chunks", lambda *a, **k: [])
    doc = f"The handbook lives at {DOC_URL} for everyone. " * 5
    conv, body = _post(
        alice_client,
        [ToolProposal("search_documents", {"query": "handbook"}, 0, 0),
         Reply(f"See [the handbook]({DOC_URL}).", 0, 0)],
        content="find the handbook",
        doc=doc,
    )
    assert body["sources"] == []
    assert body["tool_used"]["name"] == "search_documents"
    assert f"[the handbook]({DOC_URL})" in body["reply"]
    assert f"[the handbook]({DOC_URL})" in _stored_reply(conv)


def test_tool_results_are_not_document_derived_by_default():
    # Fail closed: a tool result (note-confirmation included) only feeds the
    # allowlist when the tool marks its chunks as document text.
    from app.tools import ToolResult

    assert ToolResult(content="c", summary="s", chunks=[]).document_derived is False
