"""Week 7: the tool registry. The model may PROPOSE a tool; the application
authorizes, validates, and executes it. Tools carry no tenant argument — org and
identity come from the request, never the model — and each executes on the
caller's RLS-scoped connection. Registry entries are declarative so adding a tool
is data, not control flow.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from .provenance import RetrievedChunk


class SearchDocumentsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(max_length=1000)


class CreateNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=200)
    body: str = Field(default="", max_length=4000)


@dataclass
class ToolContext:
    conn: object             # caller's RLS-scoped psycopg connection
    user: object             # auth.User
    org_id: str              # the conversation's tenant — never model-chosen
    embedder: object         # injected; tests pass FakeEmbedder


@dataclass(frozen=True)
class ToolResult:
    content: str                     # what the model reads (RETRIEVED tier)
    summary: str                     # what the audit stores (redacted at write)
    chunks: list[RetrievedChunk]     # what re-enters assemble_chat_prompt
    ref: str | None = None           # id of what the tool created, if anything


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    authorize: Callable      # (ctx: ToolContext, correlation_id, args) -> None; raises to deny
    # execute(ctx, args) -> ToolResult. Runs on ctx.conn (the caller's RLS-scoped
    # connection). An executor that writes must commit its own work; the pipeline's
    # trajectory finalize runs on the same connection afterward.
    execute: Callable
    # Week 8: when true the proposal is parked as a pending action and executes only
    # after the requester approves it. Registry data, never model-chosen.
    requires_approval: bool = False


def validate_args(spec: ToolSpec, raw: dict) -> BaseModel:
    return spec.args_model.model_validate(raw)


def anthropic_tools() -> list[dict]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.args_model.model_json_schema(),
        }
        for spec in REGISTRY.values()
    ]


# Authorize/execute hooks. Imports are lazy so this module has no import-time
# dependency on the execution or authz layers (and no import cycle with them).

def _authorize_search(ctx, correlation_id, args):
    from .authz import require_membership
    require_membership(ctx.conn, correlation_id, ctx.user.id, ctx.org_id, "tool_search_documents")


def _authorize_create_note(ctx, correlation_id, args):
    from .authz import require_membership
    require_membership(ctx.conn, correlation_id, ctx.user.id, ctx.org_id, "tool_create_note")


def _execute_search(ctx, args) -> ToolResult:
    from .tool_exec import search_documents_exec
    return search_documents_exec(ctx, args)


def _execute_create_note(ctx, args) -> ToolResult:
    from .tool_exec import create_note_exec
    return create_note_exec(ctx, args)


REGISTRY: dict[str, ToolSpec] = {
    "search_documents": ToolSpec(
        name="search_documents",
        description=(
            "Search the organization's documents for text relevant to a query and "
            "return the matching passages. Use when the user asks about the contents "
            "of their documents."
        ),
        args_model=SearchDocumentsArgs,
        authorize=_authorize_search,
        execute=_execute_search,
    ),
    "create_note": ToolSpec(
        name="create_note",
        description=(
            "Create a note in the organization's shared notebook with a title and "
            "optional body. Use only when the user explicitly asks to save or record "
            "a note."
        ),
        args_model=CreateNoteArgs,
        authorize=_authorize_create_note,
        execute=_execute_create_note,
        requires_approval=True,
    ),
}


# Defense-in-depth against future drift: the confused-deputy protection relies on
# every tool's args model forbidding extra fields, so a model-injected org_id or
# user_id is rejected at validation. Enforce it at import so a new tool that forgets
# extra="forbid" fails loudly here rather than silently accepting tenant injection.
for _spec in REGISTRY.values():
    if _spec.args_model.model_config.get("extra") != "forbid":
        raise RuntimeError(
            f"tool {_spec.name!r}: args model must set model_config extra='forbid'"
        )
