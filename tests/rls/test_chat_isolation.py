"""Database-layer proof that conversations are private to their owner."""

import uuid

import psycopg
import pytest

from .conftest import DB_URL, RlsConn, _create_user, _supabase_reachable


@pytest.fixture(scope="session")
def chat_people():
    if not _supabase_reachable():
        pytest.skip("local Supabase stack not reachable")
    alice_id, bob_id = _create_user("alice"), _create_user("bob")
    with psycopg.connect(DB_URL) as conn:  # superuser: seed bypassing RLS
        org_id = str(uuid.uuid4())
        conn.execute("insert into organizations (id, name) values (%s, 'Org B')", (org_id,))
        conn.execute(
            "insert into memberships (user_id, org_id, role) values (%s, %s, 'owner')",
            (bob_id, org_id),
        )
        conv_id = str(uuid.uuid4())
        conn.execute(
            "insert into conversations (id, user_id, org_id) values (%s, %s, %s)",
            (conv_id, bob_id, org_id),
        )
        conn.execute(
            "insert into messages (conversation_id, role, content) values (%s, 'user', %s)",
            (conv_id, "bob secret"),
        )
        conn.commit()
    return {"alice": alice_id, "bob": bob_id, "conversation": conv_id}


def test_alice_cannot_see_bob_conversation(chat_people):
    with RlsConn(chat_people["alice"]) as c:
        rows = c.execute("select id from conversations").fetchall()
    assert rows == []


def test_alice_cannot_read_bob_messages(chat_people):
    conv = chat_people["conversation"]
    with RlsConn(chat_people["alice"]) as c:
        rows = c.execute(
            "select content from messages where conversation_id = %s", (conv,)
        ).fetchall()
    assert rows == []


def test_alice_cannot_inject_message_into_bob_conversation(chat_people):
    conv = chat_people["conversation"]
    with RlsConn(chat_people["alice"]) as c:
        try:
            c.execute(
                "insert into messages (conversation_id, role, content) values (%s, 'user', 'x')",
                (conv,),
            )
            injected = True
        except psycopg.Error:
            injected = False
    assert injected is False
