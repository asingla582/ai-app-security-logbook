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


@dataclass
class ToolProposal:
    name: str
    raw_args: dict
    input_tokens: int
    output_tokens: int


class Gateway(Protocol):
    def complete(self, system: str, messages: list[Message]) -> Reply: ...

    def propose(
        self, system: str, messages: list[Message], tools: list[dict]
    ) -> "ToolProposal | Reply": ...


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

    def propose(
        self, system: str, messages: list[Message], tools: list[dict]
    ) -> "ToolProposal | Reply":
        # Offer the tools; the model either asks to use one (tool_use block) or
        # answers directly (text blocks). We take only the first tool_use — the
        # turn is single-step by design, so we never send back a tool_result.
        response = self._client_or_init().messages.create(
            model=CHAT_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
        )
        for block in response.content:
            if block.type == "tool_use":
                return ToolProposal(
                    name=block.name,
                    raw_args=dict(block.input),
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
        text = "".join(block.text for block in response.content if block.type == "text")
        return Reply(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class FakeGateway:
    # Echoes the user turn so tests can also assert output-side redaction. An
    # optional script lets a test drive the tool pipeline deterministically: each
    # propose()/complete() call pops the next scripted ToolProposal or Reply.
    def __init__(self, script: list | None = None) -> None:
        self._script = list(script or [])

    def complete(self, system: str, messages: list[Message]) -> Reply:
        # getattr guard: subclasses in tests may override __init__ without a script.
        script = getattr(self, "_script", None)
        if script:
            return script.pop(0)
        last_user = messages[-1]["content"] if messages else ""
        return Reply(text=f"Echo: {last_user}", input_tokens=0, output_tokens=0)

    def propose(
        self, system: str, messages: list[Message], tools: list[dict]
    ) -> "ToolProposal | Reply":
        script = getattr(self, "_script", None)
        if script:
            return script.pop(0)
        # No script and no tool intent: behave like a direct answer. Delegating to
        # complete() means a test gateway that overrides only complete() still has its
        # crafted reply returned through the propose-first path.
        return self.complete(system, messages)


def get_gateway() -> Gateway:
    # Server-side selection only; tests override this dependency with the fake.
    return AnthropicGateway()
