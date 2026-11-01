"""record_model_call is SECURITY DEFINER and writes audit rows for any org id it is
handed, so it must check that the caller belongs to that org (RR-W8-4)."""

import uuid

import psycopg
import pytest

from .conftest import DB_URL, _create_user, _supabase_reachable
from .test_pending_actions import UserConn

pytestmark = pytest.mark.skipif(
    not _supabase_reachable(), reason="local Supabase stack not reachable"
)

_CALL = """
select record_model_call(%s, %s, null, 'm', 'p', 'ri', 'ro', 1, 2,
  '[]'::jsonb, '[]'::jsonb, '{}'::jsonb)
"""


def _org_with_member(tag):
    user_id = _create_user(tag)
    org_id = str(uuid.uuid4())
    with psycopg.connect(DB_URL) as conn:
        conn.execute("insert into organizations (id, name) values (%s, %s)", (org_id, tag))
        conn.execute(
            "insert into memberships (user_id, org_id, role) values (%s, %s, 'owner')",
            (user_id, org_id),
        )
        conn.commit()
    return user_id, org_id


def _count(cid):
    with psycopg.connect(DB_URL) as conn:
        return conn.execute(
            "select count(*) from model_calls where correlation_id = %s", (cid,)
        ).fetchone()[0]


def test_non_member_cannot_record_for_another_org():
    _, victim_org = _org_with_member("rmc-victim")
    attacker, _ = _org_with_member("rmc-attacker")
    cid = f"rmc-{uuid.uuid4().hex}"
    with UserConn(attacker) as c:
        with pytest.raises(psycopg.errors.Error):
            c.execute(_CALL, (cid, victim_org))
    assert _count(cid) == 0


def test_member_can_record_for_own_org():
    user, org = _org_with_member("rmc-member")
    cid = f"rmc-{uuid.uuid4().hex}"
    with UserConn(user) as c:
        c.execute(_CALL, (cid, org))
        c.commit()
    assert _count(cid) == 1
