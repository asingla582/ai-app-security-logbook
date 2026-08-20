"""Provider-agnostic model gateway: real Anthropic impl and a fake for tests."""

from dataclasses import dataclass
from typing import Protocol

import anthropic

from .config import CHAT_MODEL, MAX_OUTPUT_TOKENS

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
        # Lazy: a missing key fails at request time, not import time.
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, messages: list[Message]) -> Reply:
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
    # Echoes the user turn so tests can also assert output-side redaction.
    def complete(self, system: str, messages: list[Message]) -> Reply:
        last_user = messages[-1]["content"] if messages else ""
        return Reply(text=f"Echo: {last_user}", input_tokens=0, output_tokens=0)


def get_gateway() -> Gateway:
    # Server-side selection only; tests override this dependency with the fake.
    return AnthropicGateway()
