import anthropic
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import User, get_current_user
from .authz import require_conversation_owner
from .config import CHAT_MODEL, HISTORY_WINDOW, MAX_INPUT_CHARS
from .db import db_for_user
from .gateway import Gateway, get_gateway
from .redaction import redact

router = APIRouter()

# Server-side only; the client cannot supply or override this.
SYSTEM_PROMPT = (
    "You are a helpful assistant inside a secure enterprise application. "
    "Answer the user's questions clearly and concisely."
)


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

    model_messages = [{"role": r[0], "content": r[1]} for r in reversed(history)]

    try:
        reply = gateway.complete(SYSTEM_PROMPT, model_messages)
    except anthropic.APIError:
        # Error path redacts too: no raw content reaches the audit or the response.
        with db_for_user(user.id) as conn:
            conn.execute(
                "select record_model_call(%s, %s, %s, %s, %s, %s, %s, %s)",
                (cid, org_id, conversation_id, CHAT_MODEL, redact(payload.content),
                 "[model error]", 0, 0),
            )
            conn.commit()
        raise HTTPException(status_code=502, detail="the assistant is unavailable") from None

    with db_for_user(user.id) as conn:
        conn.execute(
            "insert into messages (conversation_id, role, content) values (%s, 'assistant', %s)",
            (conversation_id, reply.text),
        )
        conn.execute(
            "select record_model_call(%s, %s, %s, %s, %s, %s, %s, %s)",
            (cid, org_id, conversation_id, CHAT_MODEL, redact(payload.content),
             redact(reply.text), reply.input_tokens, reply.output_tokens),
        )
        conn.commit()

    return {"reply": reply.text}
