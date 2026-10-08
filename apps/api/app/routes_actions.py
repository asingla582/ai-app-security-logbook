"""Week 8: the human approval endpoints. The model has no route here; only the
signed-in requester can approve or deny their own pending action."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .approvals import approve_and_execute, deny, list_pending
from .auth import User, get_current_user
from .db import db_for_user
from .embeddings import Embedder, get_embedder
from .logging import audit

router = APIRouter()

_HTTP = {"not_found": 404, "already_decided": 409, "hash_mismatch": 409, "expired": 410}


class Approve(BaseModel):
    # Only the hash the card displayed. No args field: nothing the client sends can
    # change what executes.
    model_config = ConfigDict(extra="forbid")
    args_sha256: str


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
        outcome = approve_and_execute(conn, user, cid, action_id, payload.args_sha256, embedder)
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
        outcome = deny(conn, action_id)
    audit(cid, user.id, None, f"deny_action:{action_id}", outcome)
    if outcome in _HTTP:
        raise HTTPException(status_code=_HTTP[outcome], detail=outcome)
    return {"status": "denied"}
