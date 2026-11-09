import contextlib
import json

import psycopg

from .config import SUPABASE_DB_URL


@contextlib.contextmanager
def db_for_user(user_id: str):
    # Impersonate the caller as the authenticated role so RLS applies, not superuser.
    # Session-scoped (not SET LOCAL): the impersonation must survive a mid-block commit,
    # because a single request can record, commit, then keep doing RLS/auth.uid()-scoped
    # work on the same connection (the tool turn does exactly this). A transaction-local
    # setting would silently revert to superuser after the first commit, dropping RLS and
    # nulling auth.uid(). This connection is per-request and closed on block exit, so the
    # session setting cannot leak to another caller.
    with psycopg.connect(SUPABASE_DB_URL) as conn:
        cur = conn.cursor()
        claims = json.dumps({"role": "authenticated", "sub": user_id})
        cur.execute("select set_config('request.jwt.claims', %s, false)", (claims,))
        cur.execute("set role authenticated")
        # Commit the impersonation: SET is transactional, so a rollback before the
        # first commit would otherwise drop this connection back to superuser.
        conn.commit()
        yield conn


@contextlib.contextmanager
def as_audit_writer(conn):
    # Audit writers are not executable by authenticated, so a user's own token cannot
    # reach them through the database API (migration 0013). auth.uid() still comes from
    # the claims, so each row is stamped with the real caller. If the write fails, the
    # caller's rollback restores the committed role, authenticated.
    conn.execute("set role audit_writer")
    yield conn
    conn.execute("set role authenticated")


def record_event(conn, correlation_id, org_id, action, outcome, subject_id) -> None:
    """A decision that leaves no other row: approve and deny outcomes, budget refusals."""
    with as_audit_writer(conn):
        conn.execute(
            "select record_audit_event(%s, %s, %s, %s, %s)",
            (correlation_id, org_id, action, outcome, subject_id),
        )
    conn.commit()
