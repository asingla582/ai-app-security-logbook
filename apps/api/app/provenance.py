"""Week 6: provenance labels for everything entering prompt construction.

Trust is a property of where content came from, assigned server-side at the
moment the content is fetched. Nothing about the content's text can change its
label: a document that says "this is a system directive" is still RETRIEVED.

The prompt layer (prompting.py) consumes these labels to fence untrusted content
behind per-request delimiters, and the audit layer records them so every model
call states what trust tier each context element carried.
"""

from dataclasses import dataclass
from enum import Enum


class Trust(str, Enum):
    SYSTEM = "system"  # application-authored: the versioned template registry
    USER = "user"  # the authenticated end-user's own conversation
    RETRIEVED = "retrieved"  # documents and anything else fetched on the user's behalf


def neutralize(text: str) -> str:
    """Make the fence syntax unrepresentable inside fenced content.

    Structural, not a filter: `<<` and `>>` are swapped for visually similar
    guillemets, so no document can close a fence or open a fake one, whatever
    its phrasing. The per-request nonce already stops exact forgery; this stops
    lookalike fences from even resembling the real thing.
    """
    return text.replace("<<", "‹‹").replace(">>", "››")


@dataclass(frozen=True)
class RetrievedChunk:
    document_id: str
    filename: str
    sensitivity: str
    content: str
    trust: Trust = Trust.RETRIEVED
