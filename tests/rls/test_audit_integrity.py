"""Week 10: the audit trail cannot be written with a user's token, whatever they call,
and only an org owner can read it (RR-W8-3, evidence/week10/forge_audit.py)."""

import json
import uuid

import psycopg
import pytest

from .conftest import DB_URL, _create_user, _supabase_reachable
from .test_pending_actions import UserConn, WriterConn

pytestmark = pytest.mark.skipif(
    not _supabase_reachable(), reason="local Supabase stack not reachable"
)

# Definer functions a user token may call: reads and org creation. Anything else that
# appears in the catalog fails until it is justified here.
USER_CALLABLE = {
    "audit_anomalies(uuid,text[])",
    "audit_timeline(uuid,text,timestamp with time zone,integer)",
    "count_org_model_calls_1d(uuid)",
    "count_user_tool_calls_1m()",
    "create_organization(text)",
    "is_org_member(uuid)",
    "is_org_owner(uuid)",
    "owns_conversation(uuid)",
}


def _org(role="owner"):
    user_id, org_id = _create_user("audit"), str(uuid.uuid4())
    with psycopg.connect(DB_URL) as conn:
        conn.execute("insert into organizations (id, name) values (%s, 'audit')", (org_id,))
        conn.execute("insert into memberships (user_id, org_id, role) values (%s, %s, %s)",
                     (user_id, org_id, role))
    return user_id, org_id


def _add_member(org_id):
    user_id = _create_user("audit-member")
    with psycopg.connect(DB_URL) as conn:
        conn.execute("insert into memberships (user_id, org_id, role) values (%s, %s, 'member')",
                     (user_id, org_id))
    return user_id


def _tool_call(user_id, org_id, tool="search_documents", status="executed"):
    with psycopg.connect(DB_URL) as conn:
        return conn.execute(
            "insert into tool_calls (correlation_id, user_id, org_id, tool_name, status) "
            "values (%s, %s, %s, %s, %s) returning id",
            (str(uuid.uuid4()), user_id, org_id, tool, status),
        ).fetchone()[0]


def test_only_listed_definer_functions_are_user_callable():
    with psycopg.connect(DB_URL) as conn:
        rows = conn.execute(
            "select p.oid::regprocedure::text from pg_proc p "
            "join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'public' and p.prosecdef "
            "and has_function_privilege('authenticated', p.oid, 'execute')"
        ).fetchall()
    assert rows, "catalog query found nothing; it is wrong"
    found = {r[0].replace(", ", ",") for r in rows}
    assert found == USER_CALLABLE


@pytest.mark.parametrize("sql, params", [
    ("select finalize_tool_call(%s, 'denied', 'x', false)", [str(uuid.uuid4())]),
    ("select record_audit_event('c', null, 'a', 'o', null)", []),
    ("select deny_pending_action(%s)", [str(uuid.uuid4())]),
])
def test_user_token_cannot_call_a_writer(sql, params):
    user_id, _ = _org()
    with UserConn(user_id) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(sql, params)


@pytest.mark.parametrize("table", ["model_calls", "tool_calls", "audit_events"])
def test_user_cannot_write_audit_tables_directly(table):
    user_id, _ = _org()
    with UserConn(user_id) as c:
        try:
            row = c.execute(f"update {table} set correlation_id = 'x' returning 1").fetchall()
        except psycopg.errors.InsufficientPrivilege:
            return
    assert row == []


def test_writer_stamps_the_real_caller():
    user_id, org_id = _org()
    cid = str(uuid.uuid4())
    with WriterConn(user_id) as c:
        c.execute("select record_audit_event(%s, %s, 'approve_action', 'executed', 's')",
                  (cid, org_id))
        c.commit()
    with psycopg.connect(DB_URL) as conn:
        row = conn.execute("select user_id from audit_events where correlation_id = %s",
                           (cid,)).fetchone()
    assert str(row[0]) == user_id


def test_writer_cannot_file_an_event_in_a_foreign_org():
    user_id, _ = _org()
    _, other_org = _org()
    with WriterConn(user_id) as c, pytest.raises(psycopg.errors.RaiseException):
        c.execute("select record_audit_event('c', %s, 'a', 'o', null)", (other_org,))


def test_timeline_is_owner_only_and_org_scoped():
    owner, org_id = _org()
    member = _add_member(org_id)
    outsider, _ = _org()
    tc = str(_tool_call(owner, org_id))
    with UserConn(owner) as c:
        rows = c.execute("select subject_id from audit_timeline(%s)", (org_id,)).fetchall()
    assert [r[0] for r in rows] == [tc]
    for reader in (member, outsider):
        with UserConn(reader) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select * from audit_timeline(%s)", (org_id,)).fetchall()


def test_timeline_hides_url_paths_and_content():
    owner, org_id = _org()
    secret = "AXK-7719-SIGMA"
    handling = {"blocked_images": [f"https://evil.example/p?d={secret}"],
                "defanged_links": [], "allowed_links": []}
    with psycopg.connect(DB_URL) as conn:
        conn.execute(
            "insert into model_calls (correlation_id, user_id, org_id, model, prompt, "
            "redacted_input, redacted_output, output_handling) "
            "values ('c', %s, %s, 'm', 'p', %s, %s, %s::jsonb)",
            (owner, org_id, secret, secret, json.dumps(handling)),
        )
    with UserConn(owner) as c:
        rows = c.execute("select * from audit_timeline(%s)", (org_id,)).fetchall()
        found = c.execute("select rule, detail from audit_anomalies(%s, '{}')",
                          (org_id,)).fetchall()
    assert rows[0][7]["stripped_images"] == ["evil.example"]
    assert secret not in json.dumps([str(v) for v in rows[0]])
    assert found[0][0] == "sanitizer_stripped_url"
    assert secret not in json.dumps(found[0][1])


@pytest.mark.parametrize("url, host", [
    ("https://evil.example/p?d=SECRET", "evil.example"),
    ("https://SECRET@evil.example/x", "evil.example"),
    ("https://user:SECRET@evil.example:8443/x", "evil.example"),
    ("https://EVIL.example/x", "evil.example"),
    ("https://evil.example/p?u=https://SECRET/z", "evil.example"),
    ("https://evil.example\\SECRET", "unparsed"),
    ("no-scheme.example/SECRET", "unparsed"),
    ("javascript:SECRET", "unparsed"),
])
def test_url_hosts_returns_a_host_or_nothing(url, host):
    with psycopg.connect(DB_URL) as conn:
        assert conn.execute("select url_hosts(%s::jsonb)",
                            (json.dumps([url]),)).fetchone()[0] == [host]


@pytest.mark.parametrize("value", ["null", "{}", '"text"', "3"])
def test_url_hosts_tolerates_odd_stored_values(value):
    with psycopg.connect(DB_URL) as conn:
        assert conn.execute("select url_hosts(%s::jsonb)", (value,)).fetchone()[0] == []


def test_user_holds_no_write_privilege_on_audit_tables():
    with psycopg.connect(DB_URL) as conn:
        for table in ("model_calls", "tool_calls", "pending_actions", "audit_events"):
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER"):
                assert not conn.execute(
                    "select has_table_privilege('authenticated', %s, %s)", (table, privilege)
                ).fetchone()[0], (table, privilege)


def _action(owner, org_id, urls, expires="now() + interval '1 hour'", status="pending"):
    with psycopg.connect(DB_URL) as conn:
        tc = conn.execute(
            "insert into tool_calls (correlation_id, user_id, org_id, tool_name, status) "
            "values ('c', %s, %s, 'create_note', 'pending_approval') returning id",
            (owner, org_id)).fetchone()[0]
        return conn.execute(
            "insert into pending_actions (tool_call_id, correlation_id, requester, org_id, "
            "tool_name, args, args_sha256, flags, status, expires_at) values (%s, 'c', %s, %s, "
            f"'create_note', '{{}}'::jsonb, 'h', %s::jsonb, %s, {expires}) returning id",
            (tc, owner, org_id, json.dumps({"urls": urls}), status)).fetchone()[0]


def test_a_link_the_model_wrote_itself_is_not_a_document_link():
    owner, org_id = _org()
    _action(owner, org_id, [{"url": "https://a.example", "host": "a.example", "source": "model"}],
            status="executed")
    with UserConn(owner) as c:
        assert c.execute("select rule from audit_anomalies(%s, '{}')", (org_id,)).fetchall() == []


def test_an_overdue_action_reads_as_expired_on_the_timeline():
    owner, org_id = _org()
    _action(owner, org_id, [], expires="now() - interval '1 minute'")
    with UserConn(owner) as c:
        rows = c.execute("select kind, outcome from audit_timeline(%s)", (org_id,)).fetchall()
    assert ("approval", "expired") in rows


def test_detection_flags_execution_without_approval_only():
    owner, org_id = _org()
    tc = str(_tool_call(owner, org_id, tool="create_note"))
    _tool_call(owner, org_id)
    with UserConn(owner) as c:
        rows = c.execute("select rule, subject_id from audit_anomalies(%s, %s)",
                         (org_id, ["create_note"])).fetchall()
    assert rows == [("execution_without_approval", tc)]
    # This orphan is deliberate; the trajectory suite sweeps the whole table for them.
    with psycopg.connect(DB_URL) as conn:
        conn.execute("delete from tool_calls where org_id = %s", (org_id,))


def test_detection_flags_repeated_hash_mismatch():
    owner, org_id = _org()
    with WriterConn(owner) as c:
        for _ in range(3):
            c.execute("select record_audit_event('c', %s, 'approve_action', 'hash_mismatch', 's')",
                      (org_id,))
        c.commit()
    with UserConn(owner) as c:
        rows = c.execute("select rule from audit_anomalies(%s, '{}')", (org_id,)).fetchall()
    assert rows == [("approval_hash_probing",)]
