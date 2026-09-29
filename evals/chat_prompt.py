"""Promptfoo prompt functions that read the exact template files the API serves.

Single source of truth: the eval exercises apps/api/app/prompts/chat/*.md, not a
copy, so a passing report is evidence about the shipped prompt. The attack string
enters only as a user message, mirroring the API's structural separation.
"""

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "apps" / "api" / "app" / "prompts" / "chat"


def _messages(version: int, context: dict) -> list[dict]:
    system = (_TEMPLATES / f"v{version}.md").read_text().strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": context["vars"]["attack"]},
    ]


def chat_v1(context: dict) -> list[dict]:
    # Week 2's naive prompt, kept for the before/after evidence run.
    return _messages(1, context)


def chat_v2(context: dict) -> list[dict]:
    # Week 3's first hardened prompt; kept for before/after comparison.
    return _messages(2, context)


def chat_v3(context: dict) -> list[dict]:
    # Week 5's shipped prompt; kept for before/after comparison.
    return _messages(3, context)


def chat_v4(context: dict) -> list[dict]:
    # The shipped prompt; this is what the CI gate runs against.
    return _messages(4, context)


def chat_v4_rag(context: dict) -> list[dict]:
    """Indirect injection: the attack lives in retrieved documents, not the user
    message. Documents flow through the REAL assembly path (fences, nonce,
    provenance labels), so this measures the shipped structure, not a mock.
    Output-side sanitization is deliberately absent here: it is deterministic
    code proven by apps/api/tests/test_output_handling.py, and this suite
    measures the model+prompt layer that sits in front of it.

    vars: documents (list of {filename, content}), question (str).
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
    from app.prompting import assemble_chat_prompt
    from app.provenance import RetrievedChunk

    chunks = [
        RetrievedChunk(
            document_id=f"eval-doc-{i}",
            filename=doc["filename"],
            sensitivity=doc.get("sensitivity", "internal"),
            content=doc["content"],
        )
        for i, doc in enumerate(context["vars"]["documents"], start=1)
    ]
    prompt = assemble_chat_prompt(
        [{"role": "user", "content": context["vars"]["question"]}], context=chunks
    )
    return [{"role": "system", "content": prompt.template.system}, *prompt.messages]


def chat_v5_rag(context: dict) -> list[dict]:
    """Week 7: same real RAG assembly as chat_v4_rag, used by the tool-injection
    suite where the provider offers the tool registry and we measure whether a
    document can steer the model into proposing an action. The name reflects the
    active template at Week 7; assemble_chat_prompt always uses the ACTIVE template
    (see prompting.ACTIVE_CHAT_VERSION), so this is not version-pinned here.
    vars: documents, question."""
    return chat_v4_rag(context)
