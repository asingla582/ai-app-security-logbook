from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .auth import User, get_current_user
from .authz import require_membership
from .db import db_for_user

router = APIRouter()


class CreateNote(BaseModel):
    title: str
    body: str = ""


@router.get("/orgs/{org_id}/notes")
def list_notes(org_id: str, request: Request, user: User = Depends(get_current_user)):
    cid = request.state.correlation_id
    with db_for_user(user.id) as conn:
        require_membership(conn, cid, user.id, org_id, "list_notes")
        rows = conn.execute(
            "select id, title, body from notes where org_id = %s order by created_at",
            (org_id,),
        ).fetchall()
    return [{"id": str(r[0]), "title": r[1], "body": r[2]} for r in rows]


@router.post("/orgs/{org_id}/notes", status_code=201)
def create_note(
    org_id: str, payload: CreateNote, request: Request, user: User = Depends(get_current_user)
):
    cid = request.state.correlation_id
    with db_for_user(user.id) as conn:
        require_membership(conn, cid, user.id, org_id, "create_note")
        note = conn.execute(
            "insert into notes (org_id, title, body, created_by) values (%s, %s, %s, %s) "
            "returning id, title, body",
            (org_id, payload.title, payload.body, user.id),
        ).fetchone()
        conn.commit()
    return {"id": str(note[0]), "title": note[1], "body": note[2]}
