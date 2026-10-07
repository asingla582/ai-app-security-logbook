"""Week 8: what the approval card must show. Computed server-side when the action
is proposed, from the proposed args and the chunks the model saw that turn, and
stored on the pending row, so the human approves against exactly what was computed
then. Two signals: every URL in the action (with the document it came from, or
"model" if no document carried it), and spans of the action's text copied from a
document. Approval without these is theater: a plausible note hides a planted link.
"""

from urllib.parse import urlsplit

from pydantic import BaseModel

from .output_handling import _URL
from .provenance import RetrievedChunk

_TRAILING = ".,;:!?"
_SHINGLE = 8
_MAX_SPANS = 5
_MAX_EXCERPT = 200


def _urls(text: str) -> list[str]:
    # Sentence punctuation after a URL is prose, not part of the address; strip it on
    # both sides so the note's URL and the document's URL compare equal.
    return list(dict.fromkeys(u.rstrip(_TRAILING) for u in _URL.findall(text)))


def _text_of(args: BaseModel) -> str:
    return "\n".join(v for v in args.model_dump().values() if isinstance(v, str))


def _url_flags(text: str, chunks: list[RetrievedChunk]) -> list[dict]:
    chunk_urls = [(c.filename, set(_urls(c.content))) for c in chunks]
    flags = []
    for url in _urls(text):
        source = next((name for name, urls in chunk_urls if url in urls), "model")
        flags.append({"url": url, "host": urlsplit(url).hostname or "", "source": source})
    return flags


def _doc_spans(text: str, chunks: list[RetrievedChunk]) -> list[dict]:
    words = text.split()
    lowered = [w.lower() for w in words]
    spans = []
    for chunk in chunks:
        cw = [w.lower() for w in chunk.content.split()]
        grams = {tuple(cw[i:i + _SHINGLE]) for i in range(len(cw) - _SHINGLE + 1)}
        covered = [False] * len(words)
        for i in range(len(words) - _SHINGLE + 1):
            if tuple(lowered[i:i + _SHINGLE]) in grams:
                for j in range(i, i + _SHINGLE):
                    covered[j] = True
        i = 0
        while i < len(words):
            if not covered[i]:
                i += 1
                continue
            j = i
            while j < len(words) and covered[j]:
                j += 1
            spans.append({"source": chunk.filename,
                          "excerpt": " ".join(words[i:j])[:_MAX_EXCERPT]})
            i = j
    return spans[:_MAX_SPANS]


def approval_flags(args: BaseModel, chunks: list[RetrievedChunk]) -> dict:
    text = _text_of(args)
    return {"urls": _url_flags(text, chunks), "doc_spans": _doc_spans(text, chunks)}
