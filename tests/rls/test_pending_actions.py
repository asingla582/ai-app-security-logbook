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

    def __init__(self, user_id: str):
        self.user_id = user_id

    def __enter__(self):
        self.conn = psycopg.connect(DB_URL)
        claims = json.dumps({"role": "authenticated", "sub": self.user_id})
        self.conn.execute("select set_config('request.jwt.claims', %s, false)", (claims,))
        self.conn.execute("set role authenticated")
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
        "select * from create_pending_action('cid', %s, null, %s, 'create_note', %s::jsonb, %s::jsonb)",
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
    with UserConn(user_id) as c:
        p = _pending(c, org_id, {"title": "Hé 🚀", "body": "x"})
    with psycopg.connect(DB_URL) as conn:
        text = conn.execute(
            "select args::text from pending_actions where id = %s", (p["id"],)
        ).fetchone()[0]
    assert p["hash"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_owner_can_read_own_row_other_user_cannot(member):
    user_id, org_id = member
    other_id, _ = _seed_member()
    with UserConn(user_id) as c:
        p = _pending(c, org_id)
    with UserConn(user_id) as c:
        assert c.execute("select id from pending_actions where id = %s", (p["id"],)).fetchall()
    with UserConn(other_id) as c:
        assert c.execute("select id from pending_actions where id = %s", (p["id"],)).fetchall() == []


def test_authenticated_cannot_insert_update_or_delete(member):
    user_id, org_id = member
    with UserConn(user_id) as c:
        p = _pending(c, org_id)
    for sql, params in (
        ("insert into pending_actions (tool_call_id, correlation_id, requester, org_id, "
         "tool_name, args, args_sha256, status, expires_at) values (%s, 'x', %s, %s, "
         "'create_note', '{}'::jsonb, 'h', 'approved', now())",
         (p["tool_call_id"], user_id, org_id)),
        ("update pending_actions set args = '{\"title\":\"evil\"}'::jsonb where id = %s", (p["id"],)),
        ("delete from pending_actions where id = %s", (p["id"],)),
    ):
        with UserConn(user_id) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, params)


def test_trigger_freezes_args_even_for_the_owner_role(member):
    user_id, org_id = member
    with UserConn(user_id) as c:
        p = _pending(c, org_id)
    with psycopg.connect(DB_URL) as conn:  # superuser: bypasses grants, not triggers
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "update pending_actions set args = '{\"title\":\"evil\"}'::jsonb where id = %s",
                (p["id"],),
            )


def test_trigger_rejects_illegal_transition_and_delete(member):
    user_id, org_id = member
    with UserConn(user_id) as c:
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
    with UserConn(user_id) as c:
        p = _pending(c, org_id)
    with UserConn(other_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], p["hash"])).fetchone()[0] == "not_found"
    with UserConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], "0" * 64)).fetchone()[0] == "hash_mismatch"
        c.commit()
    assert _status(p["id"]) == "pending"
    with UserConn(user_id) as c:
        row = c.execute("select * from claim_pending_action(%s, %s)", (p["id"], p["hash"])).fetchone()
        c.commit()
    assert row[0] == "claimed" and row[1] == "create_note" and row[2] == {"title": "T", "body": "B"}
    assert _status(p["id"]) == "approved"
    with UserConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], p["hash"])).fetchone()[0] == "already_decided"


def test_claim_requires_current_membership(member):
    user_id, org_id = member
    with UserConn(user_id) as c:
        p = _pending(c, org_id)
    with psycopg.connect(DB_URL) as conn:
        conn.execute("delete from memberships where user_id = %s", (user_id,))
        conn.commit()
    with UserConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, %s)",
                         (p["id"], p["hash"])).fetchone()[0] == "not_found"
    assert _status(p["id"]) == "pending"


def test_expired_claim_marks_row_and_trajectory_expired(member):
    user_id, org_id = member
    with UserConn(user_id) as c:
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
    with UserConn(user_id) as c:
        assert c.execute("select outcome from claim_pending_action(%s, 'h')",
                         (action_id,)).fetchone()[0] == "expired"
        c.commit()
    assert _status(action_id) == "expired"
    with psycopg.connect(DB_URL) as conn:
        assert conn.execute("select status from tool_calls where id = %s", (tc,)).fetchone()[0] == "expired"


def test_concurrent_claims_have_exactly_one_winner(member):
    user_id, org_id = member
    with UserConn(user_id) as c:
        p = _pending(c, org_id)
    barrier, outcomes = threading.Barrier(2), []

    def claim():
        with UserConn(user_id) as c:
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
    with UserConn(user_id) as c:
        p = _pending(c, org_id)
        c.execute("select complete_pending_action(%s, true, null, 'x')", (p["id"],))
        c.commit()
    assert _status(p["id"]) == "pending"


def test_deny_and_expire_my(member):
    user_id, org_id = member
    with UserConn(user_id) as c:
        p = _pending(c, org_id)
        assert c.execute("select deny_pending_action(%s)", (p["id"],)).fetchone()[0] == "denied"
        c.commit()
    assert _status(p["id"]) == "denied"
    with UserConn(user_id) as c:
        assert c.execute("select deny_pending_action(%s)", (p["id"],)).fetchone()[0] == "already_decided"


def test_conversation_delete_nulls_terminal_row(member):
    user_id, org_id = member
    conv_id = str(uuid.uuid4())
    with psycopg.connect(DB_URL) as conn:
        conn.execute("insert into conversations (id, user_id, org_id) values (%s, %s, %s)",
                     (conv_id, user_id, org_id))
        conn.commit()
    with UserConn(user_id) as c:
        tc = c.execute(
            "select record_tool_proposal('cid', %s, %s, 'create_note', '{}'::jsonb, 'proposed')",
            (org_id, conv_id),
        ).fetchone()[0]
        row = c.execute(
            "select * from create_pending_action('cid', %s, %s, %s, 'create_note', '{}'::jsonb, '{}'::jsonb)",
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
    "create_pending_action(text, uuid, uuid, uuid, text, jsonb, jsonb)",
    "claim_pending_action(uuid, text)",
    "complete_pending_action(uuid, boolean, uuid, text)",
    "deny_pending_action(uuid)",
    "expire_my_pending_actions()",
    "record_tool_proposal(text, uuid, uuid, text, jsonb, text)",
    "finalize_tool_call(uuid, text, text, boolean)",
])
def test_definer_functions_executable_by_authenticated_not_anon(sig):
    if not _supabase_reachable():
        pytest.skip("local Supabase stack not reachable")
    with psycopg.connect(DB_URL) as conn:
        anon, authed = conn.execute(
            "select has_function_privilege('anon', %s, 'execute'), "
            "has_function_privilege('authenticated', %s, 'execute')", (sig, sig)
        ).fetchone()
    assert anon is False and authed is True
