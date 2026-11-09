"""Week 10: the audit timeline. Org owners only, and the database enforces it too:
the read functions check ownership themselves, so RLS-bypassing definer reads stay
scoped even if this route's check were removed."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.errors import InsufficientPrivilege

from .auth import User, get_current_user
from .authz import _is_uuid
from .db import db_for_user
from .logging import audit
from .tools import REGISTRY

router = APIRouter()


def _approval_tools() -> list[str]:
    return [spec.name for spec in REGISTRY.values() if spec.requires_approval]


@router.get("/orgs/{org_id}/audit")
def audit_timeline(
    org_id: str,
    request: Request,
    correlation_id: str | None = Query(default=None, max_length=64, pattern=r"^[\w.:-]+$"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
):
    cid = request.state.correlation_id
    if not _is_uuid(org_id):
        raise HTTPException(status_code=404, detail="not found")
    with db_for_user(user.id) as conn:
        try:
            events = conn.execute(
                "select ts, kind, correlation_id, user_id, action, outcome, subject_id, detail "
                "from audit_timeline(%s, %s, null, %s)",
                (org_id, correlation_id, limit),
            ).fetchall()
            anomalies = conn.execute(
                "select rule, severity, ts, correlation_id, user_id, subject_id, detail "
                "from audit_anomalies(%s, %s)",
                (org_id, _approval_tools()),
            ).fetchall()
        except InsufficientPrivilege:
            audit(cid, user.id, org_id, "read_audit", "deny")
            # 404, not 403: do not confirm the org exists to a non-owner.
            raise HTTPException(status_code=404, detail="not found") from None
    audit(cid, user.id, org_id, "read_audit", "allow")
    return {
        "events": [
            {"ts": r[0].isoformat(), "kind": r[1], "correlation_id": r[2],
             "user_id": str(r[3]), "action": r[4], "outcome": r[5], "subject_id": r[6],
             "detail": r[7]}
            for r in events
        ],
        "anomalies": [
            {"rule": r[0], "severity": r[1], "ts": r[2].isoformat(), "correlation_id": r[3],
             "user_id": str(r[4]), "subject_id": r[5], "detail": r[6]}
            for r in anomalies
        ],
    }
