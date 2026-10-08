import json

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import User, get_current_user
from .authz import require_conversation_owner
from .config import CHAT_MODEL, HISTORY_WINDOW, MAX_INPUT_CHARS
from .db import db_for_user
from .embeddings import Embedder, get_embedder
from .gateway import Gateway, get_gateway
from .limits import RateLimited, check_org_model_budget
from .output_handling import allowed_urls_from_chunks, sanitize_output
from .prompting import assemble_chat_prompt
from .redaction import redact
from .retrieval import retrieve_chunks
from .tool_exec import run_tool_turn

router = APIRouter()


class CreateConversation(BaseModel):
    pass


class PostMessage(BaseModel):
    content: str


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, user: User = Depends(get_current_user)):
    with db_for_user(user.id) as conn:
        org = conn.execute(
            "select org_id from memberships where user_id = %s limit 1", (user.id,)
        ).fetchone()
        if org is None:
            raise HTTPException(status_code=400, detail="join an organization first")
        row = conn.execute(
            "insert into conversations (user_id, org_id) values (%s, %s) returning id, title",
            (user.id, org[0]),
        ).fetchone()
        conn.commit()
    return {"id": str(row[0]), "title": row[1]}


@router.get("/conversations")
def list_conversations(user: User = Depends(get_current_user)):
    with db_for_user(user.id) as conn:
        rows = conn.execute(
            "select id, title from conversations order by updated_at desc"
        ).fetchall()
    return [{"id": str(r[0]), "title": r[1]} for r in rows]


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str, request: Request, user: User = Depends(get_current_user)
):
    cid = request.state.correlation_id
    with db_for_user(user.id) as conn:
        require_conversation_owner(conn, cid, user.id, conversation_id, "read_conversation")
        rows = conn.execute(
            "select role, content from messages where conversation_id = %s order by created_at",
            (conversation_id,),
        ).fetchall()
    return {"id": conversation_id, "messages": [{"role": r[0], "content": r[1]} for r in rows]}


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str, request: Request, user: User = Depends(get_current_user)
):
    cid = request.state.correlation_id
    with db_for_user(user.id) as conn:
        require_conversation_owner(conn, cid, user.id, conversation_id, "delete_conversation")
        # Cascade deletes raw messages; redacted model_calls survive (conversation_id -> null).
        conn.execute("delete from conversations where id = %s", (conversation_id,))
        conn.commit()


@router.post("/conversations/{conversation_id}/messages", status_code=201)
def post_message(
    conversation_id: str,
    payload: PostMessage,
    request: Request,
    user: User = Depends(get_current_user),
    gateway: Gateway = Depends(get_gateway),
    embedder: Embedder = Depends(get_embedder),
):
    cid = request.state.correlation_id
    if len(payload.content) > MAX_INPUT_CHARS:
        raise HTTPException(status_code=413, detail="message too long")

    # Commit the user's turn before the model call so it persists even if the model errors.
    with db_for_user(user.id) as conn:
        org_id = require_conversation_owner(conn, cid, user.id, conversation_id, "send_message")
        conn.execute(
            "insert into messages (conversation_id, role, content) values (%s, 'user', %s)",
            (conversation_id, payload.content),
        )
        conn.execute(
            "update conversations set title = %s, updated_at = now() "
            "where id = %s and title = 'New conversation'",
            (payload.content[:50], conversation_id),
        )
        history = conn.execute(
            "select role, content from messages where conversation_id = %s "
            "order by created_at desc limit %s",
            (conversation_id, HISTORY_WINDOW),
        ).fetchall()
        conn.commit()

    # Denial-of-wallet ceiling (Week 7): refuse before the first model call of the
    # turn if the org has spent its daily model-call budget.
    with db_for_user(user.id) as conn:
        try:
            check_org_model_budget(conn, org_id)
        except RateLimited as limit:
            raise HTTPException(status_code=429, detail=limit.retry_hint) from None

    chunks, citations, source_ids = _retrieve(user.id, org_id, payload.content, embedder)

    # The assembler is the only path to the model: system text comes from the
    # versioned template registry; conversation content and retrieved context only
    # ever enter as messages, never the system slot.
    history_msgs = [{"role": r[0], "content": r[1]} for r in reversed(history)]
    prompt = assemble_chat_prompt(history_msgs, context=chunks or None)

    try:
        # The turn may take a single tool action; the model proposes, the app
        # authorizes/validates/executes and records the trajectory. The final answer
        # always comes from a completion with no tools offered (single-step).
        outcome = run_tool_turn(
            gateway, embedder, user, org_id, conversation_id, cid, history_msgs, chunks, prompt
        )
    except RateLimited as limit:
        # A per-user tool-rate rejection; the tool_calls row already records it.
        raise HTTPException(status_code=429, detail=limit.retry_hint) from None
    except anthropic.APIError:
        # Error path redacts too: no raw content reaches the audit or the response.
        with db_for_user(user.id) as conn:
            conn.execute(
                "select record_model_call(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
                "%s::jsonb, %s::jsonb)",
                (cid, org_id, conversation_id, CHAT_MODEL, prompt.template.ref,
                 redact(payload.content), "[model error]", 0, 0, json.dumps(source_ids),
                 json.dumps(prompt.provenance), "{}"),
            )
            conn.commit()
        raise HTTPException(status_code=502, detail="the assistant is unavailable") from None

    # Output side (Week 6): the reply is sanitized before it is stored or sent, so no
    # client ever holds an unsanitized assistant message. The allowlist is built only
    # from document-derived chunks (retrieval and search results), never from
    # app-generated ones that echo model-written arguments (week 8 final review).
    safe = sanitize_output(outcome.reply_text, allowed_urls_from_chunks(outcome.allowlist_chunks))

    with db_for_user(user.id) as conn:
        conn.execute(
            "insert into messages (conversation_id, role, content) values (%s, 'assistant', %s)",
            (conversation_id, safe.text),
        )
        conn.execute(
            "select record_model_call(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
            "%s::jsonb, %s::jsonb)",
            (cid, org_id, conversation_id, CHAT_MODEL, prompt.template.ref,
             redact(payload.content), redact(safe.text), outcome.input_tokens,
             outcome.output_tokens, json.dumps(source_ids),
             json.dumps(outcome.provenance), json.dumps(safe.actions)),
        )
        conn.commit()

    return {"reply": safe.text, "sources": citations, "tool_used": outcome.tool_used,
            "pending_action": outcome.pending_action}


def _retrieve(user_id: str, org_id: str, query: str, embedder: Embedder):
    """Chat-turn retrieval: the shared org-scoped search, plus the citation and
    source-id bookkeeping the chat response and audit lineage need.
    Returns (chunks, citations, distinct_source_ids)."""
    with db_for_user(user_id) as conn:
        chunks = retrieve_chunks(conn, org_id, query, embedder)
    citations, source_ids = [], []
    for i, chunk in enumerate(chunks, start=1):
        citations.append(
            {"source": i, "document_id": chunk.document_id, "filename": chunk.filename}
        )
        source_ids.append(chunk.document_id)
    return chunks, citations, list(dict.fromkeys(source_ids))
