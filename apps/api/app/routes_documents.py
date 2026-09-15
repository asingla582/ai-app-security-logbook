"""Week 5: document ingestion for RAG.

Documents and their chunks are org-scoped. Membership is checked at the app layer
(explicit 404 + audit) and again by RLS on insert, so a non-member can neither
reach nor write another org's knowledge base. Embedding happens only after
membership is confirmed, so a rejected upload never leaves the app.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import User, get_current_user
from .authz import require_membership
from .chunking import chunk_text
from .config import CHUNK_OVERLAP, CHUNK_SIZE, MAX_DOCUMENT_CHARS
from .db import db_for_user
from .embeddings import Embedder, get_embedder, to_pgvector

router = APIRouter()

_SENSITIVITY = {"public", "internal", "confidential"}


class CreateDocument(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str
    sensitivity: str = "internal"


@router.post("/orgs/{org_id}/documents", status_code=201)
def create_document(
    org_id: str,
    payload: CreateDocument,
    request: Request,
    user: User = Depends(get_current_user),
    embedder: Embedder = Depends(get_embedder),
):
    cid = request.state.correlation_id
    if payload.sensitivity not in _SENSITIVITY:
        raise HTTPException(status_code=422, detail="invalid sensitivity")
    if len(payload.content) > MAX_DOCUMENT_CHARS:
        raise HTTPException(status_code=413, detail="document too large")
    chunks = chunk_text(payload.content, CHUNK_SIZE, CHUNK_OVERLAP)
    if not chunks:
        raise HTTPException(status_code=422, detail="document is empty")

    # Confirm membership before embedding, so a rejected upload never leaves the app.
    with db_for_user(user.id) as conn:
        require_membership(conn, cid, user.id, org_id, "upload_document")

    embeddings = embedder.embed(chunks)

    with db_for_user(user.id) as conn:
        doc = conn.execute(
            "insert into documents (org_id, uploaded_by, filename, sensitivity) "
            "values (%s, %s, %s, %s) returning id",
            (org_id, user.id, payload.filename, payload.sensitivity),
        ).fetchone()
        for ordinal, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            conn.execute(
                "insert into document_chunks (document_id, org_id, ordinal, content, embedding) "
                "values (%s, %s, %s, %s, %s::vector)",
                (doc[0], org_id, ordinal, chunk, to_pgvector(embedding)),
            )
        conn.commit()
    return {"id": str(doc[0]), "filename": payload.filename, "sensitivity": payload.sensitivity,
            "chunks": len(chunks)}


@router.get("/orgs/{org_id}/documents")
def list_documents(org_id: str, request: Request, user: User = Depends(get_current_user)):
    cid = request.state.correlation_id
    with db_for_user(user.id) as conn:
        require_membership(conn, cid, user.id, org_id, "list_documents")
        rows = conn.execute(
            "select d.id, d.filename, d.sensitivity, d.created_at, count(c.id) "
            "from documents d left join document_chunks c on c.document_id = d.id "
            "where d.org_id = %s group by d.id order by d.created_at desc",
            (org_id,),
        ).fetchall()
    return [
        {"id": str(r[0]), "filename": r[1], "sensitivity": r[2],
         "created_at": r[3].isoformat(), "chunks": r[4]}
        for r in rows
    ]


@router.delete("/orgs/{org_id}/documents/{document_id}", status_code=204)
def delete_document(
    org_id: str, document_id: str, request: Request, user: User = Depends(get_current_user)
):
    cid = request.state.correlation_id
    try:
        uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found") from None
    with db_for_user(user.id) as conn:
        require_membership(conn, cid, user.id, org_id, "delete_document")
        # RLS scopes the delete to the caller's org; chunks cascade.
        conn.execute("delete from documents where id = %s and org_id = %s", (document_id, org_id))
        conn.commit()
