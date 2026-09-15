"""Embedding gateway: real OpenAI impl and a deterministic fake for tests.

Mirrors the model gateway. Document text is sent to OpenAI at embed time, a
third-party data-egress documented in the threat model. The fake keeps tests and CI
offline and keyless. Note the embedder never sees tenant scoping: retrieval
authorization is enforced by the database (RLS), never by the embedding provider.
"""

import hashlib
from typing import Protocol

from .config import EMBEDDING_DIM, EMBEDDING_MODEL

Vector = list[float]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[Vector]: ...


class OpenAIEmbedder:
    def __init__(self) -> None:
        self._client = None

    def _client_or_init(self):
        # Lazy: a missing key or missing package fails at request time, not import.
        if self._client is None:
            import openai

            self._client = openai.OpenAI()
        return self._client

    def embed(self, texts: list[str]) -> list[Vector]:
        if not texts:
            return []
        response = self._client_or_init().embeddings.create(model=EMBEDDING_MODEL, input=texts)
        return [item.embedding for item in response.data]


class FakeEmbedder:
    """Deterministic, offline, correct-dimension vectors. Not semantically
    meaningful, but stable so tests are repeatable and never call a network."""

    def embed(self, texts: list[str]) -> list[Vector]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> Vector:
        out: Vector = []
        counter = 0
        while len(out) < EMBEDDING_DIM:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            for byte in digest:
                # Finite floats in [-0.5, 0.5]; pgvector rejects NaN/inf.
                out.append((byte / 255.0) - 0.5)
                if len(out) >= EMBEDDING_DIM:
                    break
            counter += 1
        return out


def get_embedder() -> Embedder:
    # Server-side selection only; tests override this dependency with the fake.
    return OpenAIEmbedder()


def to_pgvector(vector: Vector) -> str:
    """Render a vector as a pgvector literal for a ::vector cast."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"
