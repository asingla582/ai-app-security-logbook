import contextlib
import json

import psycopg

from .config import SUPABASE_DB_URL


@contextlib.contextmanager
def db_for_user(user_id: str):
    # Impersonate the caller as the authenticated role so RLS applies, not superuser.
    with psycopg.connect(SUPABASE_DB_URL) as conn:
        cur = conn.cursor()
        claims = json.dumps({"role": "authenticated", "sub": user_id})
        cur.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        cur.execute("set local role authenticated")
        yield conn
