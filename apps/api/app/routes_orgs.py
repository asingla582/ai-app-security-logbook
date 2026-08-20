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
    # security definer function is the only way to create an org; direct inserts are
    # RLS-blocked, which is what stops a user granting themselves into another org.
    with db_for_user(user.id) as conn:
        org_id = conn.execute("select create_organization(%s)", (payload.name,)).fetchone()[0]
        conn.commit()
    return {"id": str(org_id), "name": payload.name}


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
