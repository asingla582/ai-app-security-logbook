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
        yield conn
