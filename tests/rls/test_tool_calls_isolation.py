"""Database-layer proof that tool_calls audit rows are not readable by end users."""

import uuid

import psycopg
import pytest

from .conftest import DB_URL, RlsConn, _create_user, _supabase_reachable


@pytest.fixture(scope="session")
def tool_call_row():
    if not _supabase_reachable():
        pytest.skip("local Supabase stack not reachable")
    user_id = _create_user("alice")
    # Seed via a superuser direct INSERT (not record_tool_proposal): the isolation
    # property must hold for any row however it was written, so the test asserts on
    # the read side regardless of the write path.
    with psycopg.connect(DB_URL) as conn:  # superuser: seed bypassing RLS
        org_id = str(uuid.uuid4())
        conn.execute("insert into organizations (id, name) values (%s, 'Org A')", (org_id,))
        conn.execute(
            "insert into memberships (user_id, org_id, role) values (%s, %s, 'owner')",
            (user_id, org_id),
        )
        conn.execute(
            "insert into tool_calls (correlation_id, user_id, org_id, tool_name, status) "
            "values ('cid', %s, %s, 'create_note', 'proposed')",
            (user_id, org_id),
        )
        conn.commit()
    return {"user": user_id}


def test_authenticated_cannot_read_tool_calls(tool_call_row):
    # tool_calls is audit, handled exactly like model_calls: RLS is enabled with no
    # policy for authenticated, so the end-user role sees zero rows however many exist
    # (Supabase's default ACL grants table SELECT, and RLS with no policy filters all
    # of it). The row seeded above is real; this user simply cannot see it.
    with RlsConn(tool_call_row["user"]) as c:
        rows = c.execute("select id from tool_calls").fetchall()
    assert rows == []
