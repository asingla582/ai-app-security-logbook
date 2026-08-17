import contextlib
import json

import psycopg

from .config import SUPABASE_DB_URL


@contextlib.contextmanager
def db_for_user(user_id: str):
    # Set the JWT claims the way PostgREST would, so RLS policies see the caller's
    # auth.uid(). The connection acts as the authenticated user, not as a superuser,
    # so tenant isolation is enforced by the database on every query in this block.
    with psycopg.connect(SUPABASE_DB_URL) as conn:
        cur = conn.cursor()
        claims = json.dumps({"role": "authenticated", "sub": user_id})
        cur.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        cur.execute("set local role authenticated")
        yield conn
