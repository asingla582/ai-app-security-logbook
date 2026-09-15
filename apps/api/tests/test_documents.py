"""Week 5 ingestion: documents are org-scoped at the API, and a non-member can
neither upload into, list, nor reach another org's knowledge base."""

import os

import psycopg


def _own_org(client) -> str:
    return client.post("/orgs", json={"name": "A"}).json()["id"]


def test_upload_creates_document_and_chunks(alice_client):
    org = _own_org(alice_client)
    r = alice_client.post(
        f"/orgs/{org}/documents",
        json={"filename": "handbook.md", "content": "x " * 2000, "sensitivity": "confidential"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["chunks"] >= 2 and body["sensitivity"] == "confidential"

    listing = alice_client.get(f"/orgs/{org}/documents").json()
    assert len(listing) == 1 and listing[0]["filename"] == "handbook.md"
    assert listing[0]["chunks"] == body["chunks"]


def test_empty_document_rejected(alice_client):
    org = _own_org(alice_client)
    r = alice_client.post(f"/orgs/{org}/documents", json={"filename": "empty.md", "content": "   "})
    assert r.status_code == 422


def test_alice_cannot_upload_into_bob_org(alice_client, bob_org_id):
    _own_org(alice_client)
    r = alice_client.post(
        f"/orgs/{bob_org_id}/documents",
        json={"filename": "sneak.md", "content": "hello world"},
    )
    assert r.status_code == 404


def test_alice_cannot_list_bob_documents(alice_client, bob_org_id):
    _own_org(alice_client)
    assert alice_client.get(f"/orgs/{bob_org_id}/documents").status_code == 404


def test_malformed_document_id_delete_is_404(alice_client):
    org = _own_org(alice_client)
    assert alice_client.delete(f"/orgs/{org}/documents/not-a-uuid").status_code == 404


def test_uploaded_chunks_are_scoped_to_the_org(alice_client):
    # Belt and suspenders: the chunks land under the caller's org id, not loose.
    org = _own_org(alice_client)
    doc_id = alice_client.post(
        f"/orgs/{org}/documents", json={"filename": "d.md", "content": "alpha beta " * 500}
    ).json()["id"]
    db_url = os.environ.get(
        "SUPABASE_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )
    with psycopg.connect(db_url) as conn:
        rows = conn.execute(
            "select distinct org_id from document_chunks where document_id = %s", (doc_id,)
        ).fetchall()
    assert len(rows) == 1 and str(rows[0][0]) == org
