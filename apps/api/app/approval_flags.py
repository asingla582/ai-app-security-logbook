"""Week 8: what the approval card must show. Computed server-side when the action
is proposed, from the proposed args and the chunks the model saw that turn, and
stored on the pending row, so the human approves against exactly what was computed
then. Two signals: every URL in the action (with the document it came from, or
"model" if no document carried it), and spans of the action's text copied from a
document. Approval without these is theater: a plausible note hides a planted link.
"""

import re
from urllib.parse import urlsplit

from pydantic import BaseModel

from .output_handling import _URL
from .provenance import RetrievedChunk

_TRAILING = ".,;:!?"
_SHINGLE = 8
_MAX_SPANS = 5
_MAX_EXCERPT = 200


# Local, case-insensitive copy: the output sanitizer's _URL stays untouched.
_URL_ANY_CASE = re.compile(_URL.pattern, re.IGNORECASE)
_TOKEN_EDGE = ".,;:!?)]>\"'`*"
_TOKEN_LEAD = "(<[\"'`*"


def _urls(text: str) -> list[str]:
    # Sentence punctuation after a URL is prose, not part of the address; strip it on
    # both sides so the note's URL and the document's URL compare equal.
    return list(dict.fromkeys(u.rstrip(_TRAILING) for u in _URL_ANY_CASE.findall(text)))


def _text_of(args: BaseModel) -> str:
    return "\n".join(v for v in args.model_dump().values() if isinstance(v, str))


def _split(url: str):
    # urlsplit raises ValueError on some hostile netlocs (e.g. a fullwidth "#" that
    # NFKC-normalizes to a delimiter). A document must not be able to crash the
    # proposal, so every split goes through here; None means malformed.
    try:
        return urlsplit(url)
    except ValueError:
        return None


def _path_key(parts) -> tuple[str, str, str]:
    path = parts.path[:-1] if parts.path.endswith("/") else parts.path
    return (parts.hostname or "", path, parts.query)


def _key(url: str) -> tuple[str, str, str, str] | None:
    # Lowercased scheme and host, one trailing "/" stripped, query kept.
    parts = _split(url)
    return None if parts is None else (parts.scheme.lower(), *_path_key(parts))


def _schemeless_hosts(chunks: list[RetrievedChunk]) -> dict[str, str]:
    hosts: dict[str, str] = {}
    for c in chunks:
        for u in _urls(c.content):
            parts = _split(u)
            host = parts.hostname if parts else None
            if host:
                hosts.setdefault(host.lower(), c.filename)
    return hosts


def _url_flags(text: str, chunks: list[RetrievedChunk]) -> list[dict]:
    chunk_keys = [(c.filename, {k for u in _urls(c.content) if (k := _key(u))},
                   set(_urls(c.content))) for c in chunks]
    flags = []
    seen: set[tuple[str, str, str]] = set()
    for url in _urls(text):
        k = _key(url)
        if k is not None:
            if k[1:] in seen:
                continue
            seen.add(k[1:])
        # Malformed URLs are never dropped: they match a chunk only by exact string.
        source = next((name for name, keys, raw in chunk_keys
                       if (k in keys if k is not None else url in raw)), "model")
        parts = _split(url)
        flags.append({"url": url, "host": (parts.hostname if parts else None) or "",
                      "source": source})
    # A note can drop the scheme ("sso-acme-verify.example/login"). Only hosts that a
    # retrieved document already mentioned are flagged; arbitrary bare domains are not.
    hosts = _schemeless_hosts(chunks)
    for raw in text.split():
        token = raw.lstrip(_TOKEN_LEAD).rstrip(_TOKEN_EDGE)
        low = token.lower()
        host = next((h for h in hosts if low == h or low.startswith(h + "/")), None)
        if host is None:
            continue
        parts = _split("//" + low)
        if parts is None:
            continue
        k = _path_key(parts)
        if k in seen:
            continue
        seen.add(k)
        flags.append({"url": token, "host": host, "source": hosts[host]})
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
