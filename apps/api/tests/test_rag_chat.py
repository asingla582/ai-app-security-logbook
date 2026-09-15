"""Week 5 retrieval, end to end through chat.

The attack: get one org's assistant to answer using another org's documents. It
cannot, because retrieval is scoped by the database to the conversation's org before
the model sees anything. These run against the deterministic fake gateway/embedder,
so they assert the authorization boundary, not answer quality.
"""

import os

import psycopg

CANARY = "BOBSECRET-launch-codes-42"


def _org_with_doc(client, org_name, filename, content):
    org = client.post("/orgs", json={"name": org_name}).json()["id"]
    client.post(f"/orgs/{org}/documents", json={"filename": filename, "content": content})
    conv = client.post("/conversations").json()["id"]
    return org, conv


def test_chat_cites_own_org_documents(alice_client):
    _, conv = _org_with_doc(alice_client, "A", "notes.md", "The quarterly plan is alpha. " * 100)
    r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "what is the plan?"})
    assert r.status_code == 201
    sources = r.json()["sources"]
    assert sources, "an org with documents should retrieve sources"
    assert all(s["filename"] == "notes.md" for s in sources)


def test_chat_records_source_lineage_in_audit(alice_client):
    _, conv = _org_with_doc(alice_client, "A", "notes.md", "alpha beta gamma " * 100)
    alice_client.post(f"/conversations/{conv}/messages", json={"content": "summarize"})
    db_url = os.environ.get(
        "SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )
    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            "select sources from model_calls where conversation_id = %s "
            "order by created_at desc limit 1",
            (conv,),
        ).fetchone()
    assert row and isinstance(row[0], list) and len(row[0]) >= 1


def test_alice_chat_cannot_retrieve_bob_documents(alice_client, bob_setup):
    # Bob uploads a document with a distinctive canary into his own org.
    bob = bob_setup["client"]
    bob.post(
        f"/orgs/{bob_setup['org_id']}/documents",
        json={"filename": "secret.md", "content": f"{CANARY} " * 50},
    )
    # Alice, in her own org, asks a question engineered to match Bob's content.
    _, conv = _org_with_doc(alice_client, "A", "mine.md", "unrelated content here " * 50)
    r = alice_client.post(
        f"/conversations/{conv}/messages", json={"content": f"tell me about {CANARY}"}
    )
    assert r.status_code == 201
    body = r.json()
    # The canary never appears in the sources, and the fake gateway echoes only the
    # question, so Bob's document text never reaches Alice's answer through retrieval.
    assert all("secret.md" != s["filename"] for s in body["sources"])
    # Prove at the source: none of Alice's cited docs belong to Bob's org.
    db_url = os.environ.get(
        "SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )
    with psycopg.connect(db_url) as conn:
        bob_docs = {
            str(r[0])
            for r in conn.execute(
                "select id from documents where org_id = %s", (bob_setup["org_id"],)
            ).fetchall()
        }
    assert not (bob_docs & {s["document_id"] for s in body["sources"]})
