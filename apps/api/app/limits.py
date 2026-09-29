"""Week 7: fixed-window rate limits counted straight from the audit tables — the
log we already trust is the source of truth, so there is no second counter to
drift. The counts go through SECURITY DEFINER functions (migration 0009) because
the audit tables are not readable by the authenticated role: a plain count on the
caller's connection would always see zero and the limits would never engage.
Known limitation (documented, not hidden): fixed windows race under concurrency;
acceptable at this scale. The org model-call ceiling is the denial-of-wallet cap:
every expensive action is a model call.
"""

from .config import MODEL_CALLS_PER_ORG_PER_DAY, TOOL_CALLS_PER_USER_PER_MINUTE


class RateLimited(Exception):
    def __init__(self, scope: str, retry_hint: str):
        super().__init__(f"rate limited: {scope}")
        self.scope = scope
        self.retry_hint = retry_hint


def check_org_model_budget(conn, org_id: str) -> None:
    # Counts prior calls only; this turn's call is not yet recorded.
    row = conn.execute("select count_org_model_calls_1d(%s)", (org_id,)).fetchone()
    if row[0] >= MODEL_CALLS_PER_ORG_PER_DAY:
        raise RateLimited("org_model_budget", "try again tomorrow")


def check_user_tool_rate(conn, user_id: str) -> None:
    row = conn.execute("select count_user_tool_calls_1m(%s)", (user_id,)).fetchone()
    if row[0] >= TOOL_CALLS_PER_USER_PER_MINUTE:
        raise RateLimited("user_tool_rate", "slow down and retry shortly")
