"""Database-layer proof of the Week 8 approval gate: pending_actions can only be
written through the SECURITY DEFINER functions, rows are immutable records, a claim
is single-use and requester-anchored, and the hash is owned by the database."""

import hashlib
import json
import threading
import uuid

import psycopg
import pytest

from .conftest import DB_URL, _create_user, _supabase_reachable


class UserConn:
    """Impersonate a user at SESSION scope, like app.db.db_for_user. The shared
    RlsConn uses SET LOCAL, which reverts to superuser at the first COMMIT (the
    Week 7 gotcha); these tests commit and keep acting as the user, so they need
    the session-scoped form or later calls silently run with auth.uid() = NULL."""

    role = "authenticated"

    def __init__(self, user_id: str):
        self.user_id = user_id

    def __enter__(self):
        self.conn = psycopg.connect(DB_URL)
        claims = json.dumps({"role": "authenticated", "sub": self.user_id})
        self.conn.execute("select set_config('request.jwt.claims', %s, false)", (claims,))
        self.conn.execute(f"set role {self.role}")
        self.conn.commit()
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()


def _seed_member():
    user_id = _create_user("hitl")
    org_id = str(uuid.uuid4())
    with psycopg.connect(DB_URL) as conn:
        conn.execute("insert into organizations (id, name) values (%s, 'Org H')", (org_id,))
        conn.execute(
            "insert into memberships (user_id, org_id, role) values (%s, %s, 'owner')",
            (user_id, org_id),
        )
        conn.commit()
    return user_id, org_id


class WriterConn(UserConn):
    """The API's write path: same claims, as audit_writer (migration 0013). Table
    reads stay on UserConn, which RLS applies to."""

    role = "audit_writer"


_WRITERS = ("record_tool_proposal", "create_pending_action", "claim_pending_action",
            "complete_pending_action", "deny_pending_action", "expire_my_pending_actions")


class _AppConnection:
    """Acts as the API does: authenticated, switching to audit_writer around a writer."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, params=None):
        if not any(fn in sql for fn in _WRITERS):
            return self._conn.execute(sql, params)
        self._conn.execute("set role audit_writer")
        result = self._conn.execute(sql, params)
        self._conn.execute("set role authenticated")
        return result


class AppConn(UserConn):
    def __enter__(self):
        return _AppConnection(super().__enter__())


def _tool_call(c, org_id):
    row = c.execute(
        "select record_tool_proposal('cid', %s, null, 'create_note', '{}'::jsonb, 'proposed')",
        (org_id,),
    ).fetchone()
    return row[0]


def _pending(c, org_id, args=None, flags=None):
    tc = _tool_call(c, org_id)
    args = args or {"title": "T", "body": "B"}
    row = c.execute(
        "select * from create_pending_action("
        "'cid', %s, null, %s, 'create_note', %s::jsonb, %s::jsonb, null)",
        (org_id, tc, json.dumps(args), json.dumps(flags or {})),
    ).fetchone()
    c.commit()
    return {"id": str(row[0]), "hash": row[1], "tool_call_id": str(tc)}


@pytest.fixture
def member():
    if not _supabase_reachable():
        pytest.skip("local Supabase stack not reachable")
    return _seed_member()


def _status(action_id):
    with psycopg.connect(DB_URL) as conn:
        return conn.execute(
            "select status from pending_actions where id = %s", (action_id,)
        ).fetchone()[0]


def test_hash_is_computed_in_sql_from_stored_args(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id, {"title": "Hé 🚀", "body": "x"})
    with psycopg.connect(DB_URL) as conn:
        text = conn.execute(
            "select args::text from pending_actions where id = %s", (p["id"],)
        ).fetchone()[0]
    assert p["hash"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_owner_can_read_own_row_other_user_cannot(member):
    user_id, org_id = member
    other_id, _ = _seed_member()
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
    with AppConn(user_id) as c:
        assert c.execute("select id from pending_actions where id = %s", (p["id"],)).fetchall()
    with AppConn(other_id) as c:
        rows = c.execute("select id from pending_actions where id = %s", (p["id"],)).fetchall()
        assert rows == []


def test_authenticated_cannot_insert_update_or_delete(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
    for sql, params in (
        ("insert into pending_actions (tool_call_id, correlation_id, requester, org_id, "
         "tool_name, args, args_sha256, status, expires_at) values (%s, 'x', %s, %s, "
         "'create_note', '{}'::jsonb, 'h', 'approved', now())",
         (p["tool_call_id"], user_id, org_id)),
        (
            "update pending_actions set args = '{\"title\":\"evil\"}'::jsonb where id = %s",
            (p["id"],),
        ),
        ("delete from pending_actions where id = %s", (p["id"],)),
    ):
        with AppConn(user_id) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, params)


def test_trigger_freezes_args_even_for_the_owner_role(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
    with psycopg.connect(DB_URL) as conn:  # superuser: bypasses grants, not triggers
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "update pending_actions set args = '{\"title\":\"evil\"}'::jsonb where id = %s",
                (p["id"],),
            )


def test_trigger_rejects_illegal_transition_and_delete(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
    with psycopg.connect(DB_URL) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("update pending_actions set status = 'executed' where id = %s", (p["id"],))
    with psycopg.connect(DB_URL) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("delete from pending_actions where id = %s", (p["id"],))


def test_claim_outcomes(member):
    user_id, org_id = member
    other_id, _ = _seed_member()
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
    with AppConn(other_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], p["hash"])).fetchone()[0] == "not_found"
    with AppConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], "0" * 64)).fetchone()[0] == "hash_mismatch"
        c.commit()
    assert _status(p["id"]) == "pending"
    with AppConn(user_id) as c:
        row = c.execute(
            "select * from claim_pending_action(%s, %s)", (p["id"], p["hash"])
        ).fetchone()
        c.commit()
    assert row[0] == "claimed" and row[1] == "create_note" and row[2] == {"title": "T", "body": "B"}
    assert _status(p["id"]) == "approved"
    with AppConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], p["hash"])).fetchone()[0] == "already_decided"


def test_claim_with_null_hash_is_a_mismatch(member):
    # NULL-safe comparison: a NULL hash must not claim the row.
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
    with AppConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], None)).fetchone()[0] == "hash_mismatch"
        c.commit()
    assert _status(p["id"]) == "pending"


def test_claim_requires_current_membership(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
    with psycopg.connect(DB_URL) as conn:
        conn.execute("delete from memberships where user_id = %s", (user_id,))
        conn.commit()
    with AppConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], p["hash"])).fetchone()[0] == "not_found"
    assert _status(p["id"]) == "pending"


def test_expired_claim_marks_row_and_trajectory_expired(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        tc = _tool_call(c, org_id)
        c.commit()
    action_id = str(uuid.uuid4())
    with psycopg.connect(DB_URL) as conn:  # INSERT is not trigger-guarded; seed an overdue row
        conn.execute(
            "insert into pending_actions (id, tool_call_id, correlation_id, requester, org_id, "
            "tool_name, args, args_sha256, status, expires_at) values (%s, %s, 'cid', %s, %s, "
            "'create_note', '{}'::jsonb, 'h', 'pending', now() - interval '1 minute')",
            (action_id, tc, user_id, org_id),
        )
        conn.commit()
    with AppConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, 'h')",
                         (action_id,)).fetchone()[0] == "expired"
        c.commit()
    assert _status(action_id) == "expired"
    with psycopg.connect(DB_URL) as conn:
        status = conn.execute("select status from tool_calls where id = %s", (tc,)).fetchone()[0]
        assert status == "expired"


def test_concurrent_claims_have_exactly_one_winner(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
    barrier, outcomes = threading.Barrier(2), []

    def claim():
        with AppConn(user_id) as c:
            barrier.wait()
            outcomes.append(c.execute("select outcome from claim_pending_action(%s, %s)",
                                      (p["id"], p["hash"])).fetchone()[0])
            c.commit()

    threads = [threading.Thread(target=claim) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes) == ["already_decided", "claimed"]


def test_complete_only_moves_an_approved_row(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
        c.execute("select complete_pending_action(%s, true, null, 'x')", (p["id"],))
        c.commit()
    assert _status(p["id"]) == "pending"


def test_deny_and_expire_my(member):
    user_id, org_id = member
    with AppConn(user_id) as c:
        p = _pending(c, org_id)
        assert c.execute("select deny_pending_action(%s)", (p["id"],)).fetchone()[0] == "denied"
        c.commit()
    assert _status(p["id"]) == "denied"
    with AppConn(user_id) as c:
        res = c.execute("select deny_pending_action(%s)", (p["id"],)).fetchone()[0]
        assert res == "already_decided"


def test_conversation_delete_nulls_terminal_row(member):
    user_id, org_id = member
    conv_id = str(uuid.uuid4())
    with psycopg.connect(DB_URL) as conn:
        conn.execute("insert into conversations (id, user_id, org_id) values (%s, %s, %s)",
                     (conv_id, user_id, org_id))
        conn.commit()
    with AppConn(user_id) as c:
        tc = c.execute(
            "select record_tool_proposal('cid', %s, %s, 'create_note', '{}'::jsonb, 'proposed')",
            (org_id, conv_id),
        ).fetchone()[0]
        row = c.execute(
            "select * from create_pending_action("
            "'cid', %s, %s, %s, 'create_note', '{}'::jsonb, '{}'::jsonb, null)",
            (org_id, conv_id, tc),
        ).fetchone()
        c.execute("select deny_pending_action(%s)", (row[0],))
        c.commit()
    with psycopg.connect(DB_URL) as conn:
        conn.execute("delete from conversations where id = %s", (conv_id,))
        conn.commit()
        assert conn.execute("select conversation_id, status from pending_actions where id = %s",
                            (row[0],)).fetchone() == (None, "denied")


@pytest.mark.parametrize("sig", [
    "create_pending_action(text, uuid, uuid, uuid, text, jsonb, jsonb, text)",
    "claim_pending_action(uuid, text)",
    "complete_pending_action(uuid, boolean, uuid, text)",
    "deny_pending_action(uuid)",
    "expire_my_pending_actions()",
    "record_tool_proposal(text, uuid, uuid, text, jsonb, text)",
    "finalize_tool_call(uuid, text, text, boolean)",
])
def test_audit_writers_executable_by_audit_writer_only(sig):
    if not _supabase_reachable():
        pytest.skip("local Supabase stack not reachable")
    with psycopg.connect(DB_URL) as conn:
        anon, authed, writer = conn.execute(
            "select has_function_privilege('anon', %s, 'execute'), "
            "has_function_privilege('authenticated', %s, 'execute'), "
            "has_function_privilege('audit_writer', %s, 'execute')", (sig, sig, sig)
        ).fetchone()
    assert (anon, authed, writer) == (False, False, True)
