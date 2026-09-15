"""Split document text into overlapping windows for embedding.

Deliberately simple for the text/markdown MVP: fixed character windows with a small
overlap so a sentence split across a boundary still has a chance of matching. Smarter
structure-aware chunking can come later; it does not affect the security boundary,
which is enforced by RLS on the chunks regardless of how they were cut.
"""


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    chunks = []
    for start in range(0, len(text), step):
        chunk = text[start : start + size].strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks
