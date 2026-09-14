"""Week 5: retrieval isolation at the database layer.

The security claim for RAG is that the database authorizes retrieval, not the model.
These tests prove it without any model in the loop: even a similarity query whose
vector is tuned to another tenant's chunk cannot return that chunk, because RLS
filters candidates to the caller's org before ranking.
"""

import psycopg
import pytest

from .conftest import DB_URL, RlsConn

DIM = 1536


def _vec(fill: float) -> str:
    return "[" + ",".join([str(fill)] * DIM) + "]"


def _seed_doc_chunk(org_id: str, uploader_id: str, fill: float, label: str) -> str:
    # Superuser insert (bypasses RLS) to place a document + chunk in an org.
    with psycopg.connect(DB_URL) as conn:
        doc = conn.execute(
            "insert into documents (org_id, uploaded_by, filename) values (%s, %s, %s) returning id",
            (org_id, uploader_id, label),
        ).fetchone()
        conn.execute(
            "insert into document_chunks (document_id, org_id, ordinal, content, embedding) "
            "values (%s, %s, 0, %s, %s::vector)",
            (doc[0], org_id, f"{label} secret content", _vec(fill)),
        )
        conn.commit()
    return str(doc[0])


@pytest.fixture(scope="module")
def rag_seed(people):
    return {
        "alice_doc": _seed_doc_chunk(people["alice"]["org_id"], people["alice"]["id"], 0.02, "alice"),
        "bob_doc": _seed_doc_chunk(people["bob"]["org_id"], people["bob"]["id"], 0.09, "bob"),
    }


def test_alice_cannot_read_bob_documents(people, rag_seed):
    bob_org = people["bob"]["org_id"]
    with RlsConn(people["alice"]["id"]) as c:
        rows = c.execute("select filename from documents where org_id = %s", (bob_org,)).fetchall()
    assert rows == []


def test_alice_cannot_read_bob_chunks(people, rag_seed):
    bob_org = people["bob"]["org_id"]
    with RlsConn(people["alice"]["id"]) as c:
        rows = c.execute(
            "select content from document_chunks where org_id = %s", (bob_org,)
        ).fetchall()
    assert rows == []


def test_crafted_query_cannot_retrieve_across_tenants(people, rag_seed):
    # Alice runs the retrieval query with a vector tuned to Bob's chunk embedding.
    # If retrieval were authorized by ranking alone, Bob's chunk would come back
    # first. RLS scopes the candidate set to Alice's org, so it never appears.
    bob_vector = _vec(0.09)
    with RlsConn(people["alice"]["id"]) as c:
        rows = c.execute(
            "select org_id, content from document_chunks "
            "order by embedding <=> %s::vector limit 5",
            (bob_vector,),
        ).fetchall()
    assert rows, "Alice should still retrieve her own chunks"
    assert all(str(r[0]) == people["alice"]["org_id"] for r in rows)
    assert all("bob" not in (r[1] or "").lower() for r in rows)


def test_alice_cannot_insert_chunk_into_bob_org(people, rag_seed):
    bob_org = people["bob"]["org_id"]
    with RlsConn(people["alice"]["id"]) as c:
        try:
            c.execute(
                "insert into document_chunks (document_id, org_id, ordinal, content, embedding) "
                "values (%s, %s, 0, 'x', %s::vector)",
                (rag_seed["bob_doc"], bob_org, _vec(0.01)),
            )
            inserted = True
        except psycopg.Error:
            inserted = False
    assert inserted is False
