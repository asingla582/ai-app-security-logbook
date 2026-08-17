import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .auth import User, get_current_user
from .authz import require_membership
from .db import db_for_user

router = APIRouter()


class CreateOrg(BaseModel):
    name: str


@router.post("/orgs", status_code=201)
def create_org(payload: CreateOrg, request: Request, user: User = Depends(get_current_user)):
    # The id is generated here rather than via INSERT...RETURNING on purpose:
    # RETURNING would apply the SELECT policy (is_org_member) to read the row back,
    # but the creator's membership does not exist until the next statement, so the
    # read-back would fail RLS. Generating the id avoids that ordering trap.
    org_id = str(uuid.uuid4())
    with db_for_user(user.id) as conn:
        conn.execute(
            "insert into organizations (id, name) values (%s, %s)",
            (org_id, payload.name),
        )
        conn.execute(
            "insert into memberships (user_id, org_id, role) values (%s, %s, 'owner')",
            (user.id, org_id),
        )
        conn.commit()
    return {"id": org_id, "name": payload.name}


@router.get("/orgs")
def list_orgs(user: User = Depends(get_current_user)):
    with db_for_user(user.id) as conn:
        rows = conn.execute("select id, name from organizations order by created_at").fetchall()
    return [{"id": str(r[0]), "name": r[1]} for r in rows]


@router.get("/orgs/{org_id}/members")
def list_members(org_id: str, request: Request, user: User = Depends(get_current_user)):
    cid = request.state.correlation_id
    with db_for_user(user.id) as conn:
        require_membership(conn, cid, user.id, org_id, "list_members")
        rows = conn.execute(
            "select user_id, role from memberships where org_id = %s",
            (org_id,),
        ).fetchall()
    return [{"user_id": str(r[0]), "role": r[1]} for r in rows]
