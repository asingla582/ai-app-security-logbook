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
    # The shipped prompt; this is what the CI gate runs against.
    return _messages(2, context)
