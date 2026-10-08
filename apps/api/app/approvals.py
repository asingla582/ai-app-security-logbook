"""Week 8: the approve path. The requester's approval claims the stored action
(single use, hash-bound to what their card showed, membership re-checked) and the
app then executes it AS THE CALLER with args read from the claimed row. Nothing
from the approve request reaches the executor except the id and the hash.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from .authz import _is_uuid
from .redaction import redact
from .tool_exec import _execute_with_timeout
from .tools import REGISTRY, ToolContext, validate_args


@dataclass(frozen=True)
class ApprovalOutcome:
    status: str               # not_found|already_decided|hash_mismatch|expired|executed|failed
    note_id: str | None = None


def _complete(conn, action_id, executed, ref, summary):
    conn.execute("select complete_pending_action(%s, %s, %s, %s)",
                 (action_id, executed, ref, redact(summary)))
    conn.commit()


def approve_and_execute(conn, user, correlation_id, action_id, args_sha256, embedder) -> ApprovalOutcome:
    if not _is_uuid(action_id):
        return ApprovalOutcome("not_found")
    row = conn.execute(
        "select outcome, tool_name, args, org_id, tool_call_id from claim_pending_action(%s, %s)",
        (action_id, args_sha256),
    ).fetchone()
    conn.commit()  # 'approved' (or 'expired') is durable before anything executes
    outcome, tool_name, stored_args, org_id = row[0], row[1], row[2], row[3]
    if outcome != "claimed":
        return ApprovalOutcome(outcome)

    spec = REGISTRY.get(tool_name)
    if spec is None or not spec.requires_approval:
        _complete(conn, action_id, False, None, "tool no longer approvable")
        return ApprovalOutcome("failed")
    try:
        args = validate_args(spec, stored_args)
    except ValidationError:
        _complete(conn, action_id, False, None, "stored arguments no longer valid")
        return ApprovalOutcome("failed")

    ctx = ToolContext(conn=conn, user=user, org_id=str(org_id), embedder=embedder)
    try:
        result = _execute_with_timeout(conn, spec, ctx, args)
    except Exception:
        conn.rollback()
        _complete(conn, action_id, False, None, "execution failed or timed out")
        return ApprovalOutcome("failed")
    _complete(conn, action_id, True, result.ref, result.summary)
    return ApprovalOutcome("executed", note_id=result.ref)


def list_pending(conn, conversation_id: str | None) -> list[dict]:
    conn.execute("select expire_my_pending_actions()")
    conn.commit()
    sql = ("select id, tool_name, args, args_sha256, flags, expires_at from pending_actions "
           "where status = 'pending'")
    params: tuple = ()
    if conversation_id is not None:
        if not _is_uuid(conversation_id):
            return []
        sql += " and conversation_id = %s"
        params = (conversation_id,)
    rows = conn.execute(sql + " order by created_at", params).fetchall()
    return [
        {"id": str(r[0]), "tool_name": r[1], "args": r[2], "args_sha256": r[3],
         "flags": r[4], "expires_at": r[5].isoformat()}
        for r in rows
    ]


def deny(conn, action_id: str) -> str:
    if not _is_uuid(action_id):
        return "not_found"
    outcome = conn.execute("select deny_pending_action(%s)", (action_id,)).fetchone()[0]
    conn.commit()
    return outcome
