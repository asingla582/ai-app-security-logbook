"""Week 7: fixed-window rate limits counted straight from the audit tables — the
log we already trust is the source of truth, so there is no second counter to
drift. Known limitation (documented, not hidden): fixed windows race under
concurrency; acceptable at this scale. The org model-call ceiling is the
denial-of-wallet cap: every expensive action is a model call.
"""

from .config import MODEL_CALLS_PER_ORG_PER_DAY, TOOL_CALLS_PER_USER_PER_MINUTE


class RateLimited(Exception):
    def __init__(self, scope: str, retry_hint: str):
        super().__init__(f"rate limited: {scope}")
        self.scope = scope
        self.retry_hint = retry_hint


def check_org_model_budget(conn, org_id: str) -> None:
    # Count prior calls only; this turn's call is not yet recorded.
    row = conn.execute(
        "select count(*) from model_calls "
        "where org_id = %s and created_at > now() - interval '1 day'",
        (org_id,),
    ).fetchone()
    if row[0] >= MODEL_CALLS_PER_ORG_PER_DAY:
        raise RateLimited("org_model_budget", "try again tomorrow")


def check_user_tool_rate(conn, user_id: str) -> None:
    row = conn.execute(
        "select count(*) from tool_calls "
        "where user_id = %s and proposed_at > now() - interval '1 minute'",
        (user_id,),
    ).fetchone()
    if row[0] >= TOOL_CALLS_PER_USER_PER_MINUTE:
        raise RateLimited("user_tool_rate", "slow down and retry shortly")
