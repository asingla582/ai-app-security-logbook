"""Week 7: the tool turn. Proposal in, authorized-and-executed action or plain
reply out. The model proposes; the app disposes — rate limit, validate, authorize,
execute as the caller under a timeout, then answer from a fresh assembly with the
result as RETRIEVED context (single-step: the second call carries no tools).

This module holds the decision logic and the two executors. Orchestration
(run_tool_turn) is wired into the chat route in the following task.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from .provenance import RetrievedChunk
from .retrieval import retrieve_chunks
from .tools import REGISTRY, ToolResult, ToolSpec, validate_args


@dataclass
class _Decision:
    status: str
    spec: ToolSpec | None = None
    args: object | None = None


def _decide_tool(proposal) -> _Decision:
    """Map a raw model proposal to a decision. An unknown tool name or arguments
    that fail validation are 'invalid' — recorded, then the turn falls back to a
    plain answer. Neither crashes the pipeline."""
    spec = REGISTRY.get(proposal.name)
    if spec is None:
        return _Decision("invalid")
    try:
        args = validate_args(spec, proposal.raw_args)
    except ValidationError:
        return _Decision("invalid")
    return _Decision("proposed", spec=spec, args=args)


def search_documents_exec(ctx, args) -> ToolResult:
    # Same query as chat retrieval, on the caller's connection; the model never
    # picked the org — ctx.org_id is the conversation's tenant. The embedder is the
    # injected one (FakeEmbedder under test), never a global.
    chunks = retrieve_chunks(ctx.conn, ctx.org_id, args.query, ctx.embedder)
    summary = f"searched documents for {args.query!r}: {len(chunks)} passage(s)"
    return ToolResult(content=summary, summary=summary, chunks=chunks)


def create_note_exec(ctx, args) -> ToolResult:
    note = ctx.conn.execute(
        "insert into notes (org_id, title, body, created_by) values (%s, %s, %s, %s) "
        "returning id",
        (ctx.org_id, args.title, args.body, ctx.user.id),
    ).fetchone()
    ctx.conn.commit()
    # returning id only (not the body, unlike routes_notes.create_note): the tool
    # result feeds back into the model, so the confirmation deliberately carries no
    # note content — just an id and title acknowledgement.
    summary = f"created note {note[0]} titled {args.title!r}"
    chunk = RetrievedChunk(
        document_id=str(note[0]),
        filename="note-confirmation",
        sensitivity="internal",
        content=summary,
    )
    return ToolResult(content=summary, summary=summary, chunks=[chunk])
