"""Shared org-scoped vector retrieval.

One query, two callers: the chat turn (routes_chat) and the search_documents tool
(tool_exec). Keeping the SQL here means retrieval's tenant scoping — RLS plus the
explicit org_id filter — and its provenance labeling live in exactly one place.
The model never chooses what it may read: org_id is the request's tenant, and
chunks come back labeled RETRIEVED, trust assigned from the database, never content.
"""

from .config import RETRIEVAL_TOP_K
from .embeddings import Embedder, to_pgvector
from .provenance import RetrievedChunk


def retrieve_chunks(conn, org_id: str, query: str, embedder: Embedder) -> list[RetrievedChunk]:
    """Top-k org-scoped chunks for the query, provenance-labeled, on the caller's
    RLS-scoped connection."""
    query_vector = embedder.embed([query])[0]
    rows = conn.execute(
        "select c.document_id, d.filename, d.sensitivity, c.content "
        "from document_chunks c join documents d on d.id = c.document_id "
        "where c.org_id = %s order by c.embedding <=> %s::vector limit %s",
        (org_id, to_pgvector(query_vector), RETRIEVAL_TOP_K),
    ).fetchall()
    return [
        RetrievedChunk(
            document_id=str(document_id),
            filename=filename,
            sensitivity=sensitivity,
            content=content,
        )
        for document_id, filename, sensitivity, content in rows
    ]
