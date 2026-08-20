"""Week 2 attack suite: conversation isolation and PII redaction in the audit log."""

import os

import psycopg


def _own_conversation(client):
    client.post("/orgs", json={"name": "A"})
    return client.post("/conversations").json()["id"]


def test_alice_cannot_read_bob_conversation(alice_client, bob_conversation):
    _own_conversation(alice_client)
    assert alice_client.get(f"/conversations/{bob_conversation}").status_code == 404


def test_alice_cannot_delete_bob_conversation(alice_client, bob_conversation):
    _own_conversation(alice_client)
    assert alice_client.delete(f"/conversations/{bob_conversation}").status_code == 404


def test_alice_cannot_post_into_bob_conversation(alice_client, bob_conversation):
    _own_conversation(alice_client)
    r = alice_client.post(f"/conversations/{bob_conversation}/messages", json={"content": "x"})
    assert r.status_code == 404


def test_oversized_message_rejected(alice_client):
    conv_id = _own_conversation(alice_client)
    r = alice_client.post(f"/conversations/{conv_id}/messages", json={"content": "x" * 9000})
    assert r.status_code == 413


def test_pii_never_lands_in_the_audit_log(alice_client):
    conv_id = _own_conversation(alice_client)
    alice_client.post(
        f"/conversations/{conv_id}/messages",
        json={"content": "my ssn is 123-45-6789 and email me at alice@example.com"},
    )
    # model_calls is not readable by end users; inspect it directly as the DB owner.
    db_url = os.environ.get(
        "SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )
    with psycopg.connect(db_url) as conn:
        rows = conn.execute(
            "select redacted_input, redacted_output from model_calls where conversation_id = %s",
            (conv_id,),
        ).fetchall()
    assert rows, "a model call should have been recorded"
    blob = " ".join(f"{r[0]} {r[1]}" for r in rows)
    assert "123-45-6789" not in blob
    assert "alice@example.com" not in blob
    assert "[SSN]" in blob and "[EMAIL]" in blob
