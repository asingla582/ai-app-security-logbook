"""The model gateway: a thin, provider-agnostic seam over the LLM.

One real implementation (Anthropic) and one deterministic fake for tests, chosen
by dependency injection on the server side. A second provider could slot in behind
this same interface later without touching the routes or the audit logic.
"""

from dataclasses import dataclass
from typing import Protocol

import anthropic

from .config import CHAT_MODEL, MAX_OUTPUT_TOKENS

# A chat turn as the model sees it: role is "user" or "assistant", content is text.
Message = dict[str, str]


@dataclass
class Reply:
    text: str
    input_tokens: int
    output_tokens: int


class Gateway(Protocol):
    def complete(self, system: str, messages: list[Message]) -> Reply: ...


class AnthropicGateway:
    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    def _client_or_init(self) -> anthropic.Anthropic:
        # Lazily construct so a missing key surfaces as a request-time error, not an
        # import/DI-time crash. The SDK reads ANTHROPIC_API_KEY from the environment.
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, messages: list[Message]) -> Reply:
        # No thinking param: on this model that keeps thinking off, which is what a
        # plain chat turn wants. max_tokens caps output cost per call.
        response = self._client_or_init().messages.create(
            model=CHAT_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=messages,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return Reply(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class FakeGateway:
    """Deterministic, free, and offline. Echoes the last user turn so tests can also
    assert that PII in model *output* is redacted in the audit log."""

    def complete(self, system: str, messages: list[Message]) -> Reply:
        last_user = messages[-1]["content"] if messages else ""
        return Reply(text=f"Echo: {last_user}", input_tokens=0, output_tokens=0)


def get_gateway() -> Gateway:
    # The real gateway at runtime. Tests override this FastAPI dependency with a
    # FakeGateway; there is no client-controllable switch, so an attacker cannot
    # force the fake in production.
    return AnthropicGateway()
