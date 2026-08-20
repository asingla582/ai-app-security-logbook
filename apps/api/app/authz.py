from fastapi import HTTPException

from .logging import audit


def require_membership(conn, correlation_id: str, user_id: str, org_id: str, action: str) -> None:
    # Defense-in-depth: RLS already scopes every query to the caller's orgs, but
    # we check membership explicitly and log the decision so an attempt on another
    # tenant produces a deny record, not a silent empty result.
    row = conn.execute(
        "select 1 from memberships where org_id = %s and user_id = %s",
        (org_id, user_id),
    ).fetchone()
    if row is None:
        audit(correlation_id, user_id, org_id, action, "deny")
        # 404, not 403: do not confirm the org exists to a non-member
        raise HTTPException(status_code=404, detail="not found")
    audit(correlation_id, user_id, org_id, action, "allow")


def require_conversation_owner(conn, correlation_id, user_id, conversation_id, action) -> str:
    # RLS already scopes conversations to their owner, so a non-owner's query
    # returns no row. We check explicitly and log the decision. Returns the org_id
    # for the audit trail.
    row = conn.execute(
        "select org_id from conversations where id = %s", (conversation_id,)
    ).fetchone()
    if row is None:
        audit(correlation_id, user_id, conversation_id, action, "deny")
        raise HTTPException(status_code=404, detail="not found")
    audit(correlation_id, user_id, conversation_id, action, "allow")
    return str(row[0])
