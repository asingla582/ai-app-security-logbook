"""Fixtures for database-level RLS tests.

These talk to Postgres directly (as the superuser connection) to set up data, then
open connections that impersonate each user the way PostgREST does — setting
request.jwt.claims and the `authenticated` role — so RLS policies see auth.uid().
Skips cleanly if no local Supabase stack is reachable.
"""

import os
import uuid

import httpx
import psycopg
import pytest

SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


def _supabase_reachable() -> bool:
    if not SERVICE_KEY:
        return False
    try:
        psycopg.connect(DB_URL).close()
        return True
    except psycopg.Error:
        return False


class RlsConn:
    """A connection acting as a specific authenticated user, subject to RLS."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def __enter__(self):
        import json

        self.conn = psycopg.connect(DB_URL)
        cur = self.conn.cursor()
        claims = json.dumps({"role": "authenticated", "sub": self.user_id})
        cur.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        cur.execute("set local role authenticated")
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()


def _create_user(tag: str) -> str:
    headers = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}
    email = f"{tag}-{uuid.uuid4().hex[:8]}@example.com"
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers=headers,
        json={"email": email, "password": "Password123!", "email_confirm": True},
    )
    r.raise_for_status()
    return r.json()["id"]


@pytest.fixture(scope="session")
def people():
    if not _supabase_reachable():
        pytest.skip("local Supabase stack not reachable")
    alice_id, bob_id = _create_user("alice"), _create_user("bob")
    out = {"alice": {"id": alice_id}, "bob": {"id": bob_id}}
    with psycopg.connect(DB_URL) as conn:  # superuser: bypasses RLS to seed
        for key, org_name in (("alice", "Org A"), ("bob", "Org B")):
            org = conn.execute(
                "insert into organizations (name) values (%s) returning id", (org_name,)
            ).fetchone()
            conn.execute(
                "insert into memberships (user_id, org_id, role) values (%s, %s, 'owner')",
                (out[key]["id"], org[0]),
            )
            conn.execute(
                "insert into notes (org_id, title, body, created_by) values (%s, %s, %s, %s)",
                (org[0], f"{org_name} secret", "loot", out[key]["id"]),
            )
            out[key]["org_id"] = str(org[0])
        conn.commit()
    return out
