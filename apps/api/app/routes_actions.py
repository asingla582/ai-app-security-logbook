"""Week 8: the human approval endpoints. The model has no route here; only the
signed-in requester can approve or deny their own pending action."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .approvals import approve_and_execute, deny, list_pending
from .auth import User, get_current_user
from .authz import _is_uuid
from .db import db_for_user, record_event
from .embeddings import Embedder, get_embedder
from .logging import audit
from .telemetry import proposal_link, traced

router = APIRouter()

_HTTP = {"not_found": 404, "already_decided": 409, "hash_mismatch": 409, "expired": 410}


class Approve(BaseModel):
    # Only the hash the card displayed. No args field: nothing the client sends can
    # change what executes.
    model_config = ConfigDict(extra="forbid")
    args_sha256: str


def _subject(action_id: str) -> str:
    # The id comes off the URL: anything but a uuid is recorded as just that, because
    # Postgres rejects a NUL and the record should not hold whatever a caller typed.
    return action_id if _is_uuid(action_id) else "malformed"


def _action_context(conn, action_id: str):
    """The org and proposal trace of the caller's own action (RLS: requester only)."""
    if not _is_uuid(action_id):
        return None, None
    row = conn.execute(
        "select org_id, trace_context from pending_actions where id = %s", (action_id,)
    ).fetchone()
    conn.commit()
    return (row[0], row[1]) if row else (None, None)


@router.get("/actions")
def get_actions(status: str = "pending", conversation_id: str | None = None,
                user: User = Depends(get_current_user)):
    if status != "pending":
        raise HTTPException(status_code=400, detail="only status=pending is supported")
    with db_for_user(user.id) as conn:
        return list_pending(conn, conversation_id)


@router.post("/actions/{action_id}/approve")
def approve(action_id: str, payload: Approve, request: Request,
            user: User = Depends(get_current_user),
            embedder: Embedder = Depends(get_embedder)):
    cid = request.state.correlation_id
    with db_for_user(user.id) as conn:
        org_id, proposal_context = _action_context(conn, action_id)
        with traced(
            "approval.decide", links=proposal_link(proposal_context)
        ) as span:
            span.set_attribute("app.action_id", _subject(action_id))
            outcome = approve_and_execute(
                conn, user, cid, action_id, payload.args_sha256, embedder
            )
            span.set_attribute("approval.outcome", outcome.status)
        record_event(conn, cid, org_id, "approve_action", outcome.status, _subject(action_id))
    audit(cid, user.id, None, f"approve_action:{action_id}", outcome.status)
    if outcome.status in _HTTP:
        raise HTTPException(status_code=_HTTP[outcome.status], detail=outcome.status)
    if outcome.status == "executed":
        return {"status": "executed", "note_id": outcome.note_id}
    return {"status": "failed"}


@router.post("/actions/{action_id}/deny")
def deny_action(action_id: str, request: Request, user: User = Depends(get_current_user)):
    cid = request.state.correlation_id
    with db_for_user(user.id) as conn:
        org_id, proposal_context = _action_context(conn, action_id)
        with traced(
            "approval.deny", links=proposal_link(proposal_context)
        ) as span:
            span.set_attribute("app.action_id", _subject(action_id))
            outcome = deny(conn, action_id)
            span.set_attribute("approval.outcome", outcome)
        record_event(conn, cid, org_id, "deny_action", outcome, _subject(action_id))
    audit(cid, user.id, None, f"deny_action:{action_id}", outcome)
    if outcome in _HTTP:
        raise HTTPException(status_code=_HTTP[outcome], detail=outcome)
    return {"status": "denied"}
