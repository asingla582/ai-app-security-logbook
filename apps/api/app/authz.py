from fastapi import HTTPException

from .logging import audit


def require_membership(conn, correlation_id: str, user_id: str, org_id: str, action: str) -> None:
    # Defense-in-depth over RLS: log an explicit deny rather than a silent empty result.
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
    # Check user_id explicitly so this holds even if RLS is ever bypassed, not just implicitly.
    row = conn.execute(
        "select org_id from conversations where id = %s and user_id = %s",
        (conversation_id, user_id),
    ).fetchone()
    if row is None:
        audit(correlation_id, user_id, conversation_id, action, "deny")
        raise HTTPException(status_code=404, detail="not found")
    audit(correlation_id, user_id, conversation_id, action, "allow")
    return str(row[0])
