"""Week 7: the tool turn. Proposal in, authorized-and-executed action or plain
reply out. The model proposes; the app disposes — rate limit, validate, authorize,
execute as the caller under a timeout, then answer from a fresh assembly with the
result as RETRIEVED context (single-step: the second call carries no tools).
Week 8: an approval-required tool is parked as a pending action instead of executed; see approvals.py for the approve path.
"""

import json
from dataclasses import dataclass, field

from fastapi import HTTPException
from pydantic import ValidationError

from .approval_flags import approval_flags
from .config import TOOL_TIMEOUT_SECONDS
from .db import db_for_user
from .gateway import Reply
from .limits import RateLimited, check_user_tool_rate
from .prompting import assemble_chat_prompt
from .provenance import RetrievedChunk
from .redaction import redact
from .retrieval import retrieve_chunks
from .tools import REGISTRY, ToolContext, ToolResult, ToolSpec, anthropic_tools, validate_args


@dataclass
class _Decision:
    status: str
    spec: ToolSpec | None = None
    args: object | None = None


@dataclass
class TurnOutcome:
    reply_text: str
    tool_used: dict | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # The chunks and provenance the FINAL model call saw, for the audit provenance.
    final_chunks: list = field(default_factory=list)
    provenance: list = field(default_factory=list)
    pending_action: dict | None = None
    # The document-derived subset of final_chunks (chat retrieval plus search
    # results). The output allowlist is built from this only: app-generated chunks
    # (pending-approval, note-confirmation) echo model-written text, and the grant is
    # what a document literally says, never what the model assembles.
    allowlist_chunks: list = field(default_factory=list)


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
    return ToolResult(content=summary, summary=summary, chunks=chunks, document_derived=True)


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
    return ToolResult(content=summary, summary=summary, chunks=[chunk], ref=str(note[0]))


def _park_for_approval(conn, correlation_id, org_id, conversation_id, proposal_id, spec, args, chunks):
    """Week 8: an approval-required tool is never executed in the proposal turn.
    The exact args are stored with a database-computed hash; the requester approves
    them in a separate request. Returns the payload the approval card renders."""
    flags = approval_flags(args, chunks)
    row = conn.execute(
        "select * from create_pending_action(%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
        (correlation_id, org_id, conversation_id, proposal_id, spec.name,
         json.dumps(args.model_dump()), json.dumps(flags)),
    ).fetchone()
    conn.commit()
    return {
        "id": str(row[0]),
        "tool_name": spec.name,
        "args": args.model_dump(),
        "args_sha256": row[1],
        "flags": flags,
        "expires_at": row[2].isoformat(),
    }


def _pending_chunk(pending) -> RetrievedChunk:
    title = pending["args"].get("title", "")
    return RetrievedChunk(
        document_id=pending["id"],
        filename="pending-approval",
        sensitivity="internal",
        content=(f"Action pending the user's approval: create a note titled {title!r}. "
                 "It has NOT been saved."),
    )


def _audit_safe(value):
    # Postgres jsonb cannot store a NUL character. The audit copy of the raw args
    # replaces it with U+FFFD so a NUL from the model is still recorded (visibly)
    # instead of failing the turn before the trajectory row exists.
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, dict):
        return {_audit_safe(k): _audit_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_audit_safe(v) for v in value]
    return value


def _record_proposal(conn, correlation_id, org_id, conversation_id, name, raw_args, status):
    row = conn.execute(
        "select record_tool_proposal(%s, %s, %s, %s, %s::jsonb, %s)",
        (correlation_id, org_id, conversation_id, name,
         redact(json.dumps(_audit_safe(raw_args))), status),
    ).fetchone()
    conn.commit()
    return row[0]


def _finalize(conn, proposal_id, status, summary, executed):
    conn.execute(
        "select finalize_tool_call(%s, %s, %s, %s)",
        (proposal_id, status, summary, executed),
    )
    conn.commit()


def _execute_with_timeout(conn, spec, ctx, args) -> ToolResult:
    # Bound the tool's DB work with a per-transaction statement timeout (Ruling 4):
    # this cancels a runaway query with a single connection owner, unlike a worker
    # thread that would share this connection with the timed-out call. Network calls
    # inside a tool (e.g. the embedder) are not bound by this, matching the existing
    # retrieval path.
    conn.execute(
        "select set_config('statement_timeout', %s, true)",
        (str(int(TOOL_TIMEOUT_SECONDS * 1000)),),
    )
    return spec.execute(ctx, args)


def run_tool_turn(
    gateway,
    embedder,
    user,
    org_id,
    conversation_id,
    correlation_id,
    history,
    retrieval_chunks,
    prompt,
):
    """One chat turn that may take a single tool action. The model proposes; the app
    authorizes, validates, executes as the caller under a timeout, and records the
    proposed -> decided -> executed trajectory. The final answer always comes from a
    completion with NO tools offered, so the turn cannot chain (single-step).

    The route assembles and passes the first-leg `prompt` (so it owns the error-path
    audit record) plus the `retrieval_chunks` behind it and the `history` needed to
    reassemble the second leg with the tool result appended."""
    first = gateway.propose(prompt.template.system, prompt.messages, anthropic_tools())
    if isinstance(first, Reply):
        return TurnOutcome(
            reply_text=first.text,
            tool_used=None,
            input_tokens=first.input_tokens,
            output_tokens=first.output_tokens,
            final_chunks=retrieval_chunks,
            provenance=prompt.provenance,
            allowlist_chunks=list(retrieval_chunks),
        )

    decision = _decide_tool(first)
    result = None
    pending = None
    with db_for_user(user.id) as conn:
        proposal_id = _record_proposal(
            conn, correlation_id, org_id, conversation_id, first.name,
            first.raw_args, decision.status,
        )
        if decision.status == "invalid":
            _finalize(conn, proposal_id, "invalid", "unknown tool or invalid arguments", False)
        else:
            ctx = ToolContext(conn=conn, user=user, org_id=org_id, embedder=embedder)
            try:
                check_user_tool_rate(conn, user.id)
            except RateLimited:
                _finalize(conn, proposal_id, "rate_limited", "per-user tool rate exceeded", False)
                raise
            try:
                decision.spec.authorize(ctx, correlation_id, decision.args)
            except HTTPException:
                _finalize(conn, proposal_id, "denied", "authorization denied", False)
                raise
            if decision.spec.requires_approval:
                try:
                    pending = _park_for_approval(
                        conn, correlation_id, org_id, conversation_id, proposal_id,
                        decision.spec, decision.args, retrieval_chunks,
                    )
                except Exception:
                    # e.g. jsonb rejecting the args. Nothing was parked and nothing
                    # runs; close the trajectory and answer plainly, as the failed
                    # execution path does.
                    conn.rollback()
                    _finalize(conn, proposal_id, "failed", "could not park for approval", False)
                    pending = None
                else:
                    _finalize(conn, proposal_id, "pending_approval",
                              redact(f"awaiting approval: {pending['id']}"), False)
            else:
                try:
                    result = _execute_with_timeout(conn, decision.spec, ctx, decision.args)
                except Exception:
                    conn.rollback()  # a timed-out/failed statement aborts the tx
                    _finalize(conn, proposal_id, "failed", "execution failed or timed out", False)
                    result = None
                else:
                    _finalize(conn, proposal_id, "executed", redact(result.summary), True)

    # Second leg, outside the tool connection: NO tools offered, so no chaining.
    if pending is not None:
        final_chunks = list(retrieval_chunks) + [_pending_chunk(pending)]
        prompt2 = assemble_chat_prompt(history, context=final_chunks)
        reply = gateway.complete(prompt2.template.system, prompt2.messages)
        return TurnOutcome(
            reply_text=reply.text,
            tool_used=None,
            input_tokens=first.input_tokens + reply.input_tokens,
            output_tokens=first.output_tokens + reply.output_tokens,
            final_chunks=final_chunks,
            provenance=prompt2.provenance,
            pending_action=pending,
            allowlist_chunks=list(retrieval_chunks),
        )

    if result is None:
        # invalid or failed proposal: answer plainly from retrieval only.
        reply = gateway.complete(prompt.template.system, prompt.messages)
        return TurnOutcome(
            reply_text=reply.text,
            tool_used=None,
            input_tokens=first.input_tokens + reply.input_tokens,
            output_tokens=first.output_tokens + reply.output_tokens,
            final_chunks=retrieval_chunks,
            provenance=prompt.provenance,
            allowlist_chunks=list(retrieval_chunks),
        )

    final_chunks = list(retrieval_chunks) + list(result.chunks)
    prompt2 = assemble_chat_prompt(history, context=final_chunks or None)
    reply = gateway.complete(prompt2.template.system, prompt2.messages)
    return TurnOutcome(
        reply_text=reply.text,
        tool_used={"name": decision.spec.name, "summary": result.summary},
        input_tokens=first.input_tokens + reply.input_tokens,
        output_tokens=first.output_tokens + reply.output_tokens,
        final_chunks=final_chunks,
        provenance=prompt2.provenance,
        allowlist_chunks=list(retrieval_chunks)
        + (list(result.chunks) if result.document_derived else []),
    )
