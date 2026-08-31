"""Versioned prompt templates and the single assembly path to the model.

Structural rule (Week 3): the system slot is filled only from the on-disk
template registry; request data can enter only as user/assistant messages.
Nothing in this module interpolates conversation content into system text,
so user input cannot become an application instruction by construction.
"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .gateway import Message

_TEMPLATE_DIR = Path(__file__).parent / "prompts"

# History rows are already constrained to these by the DB check; validating
# again here keeps the prompt layer independent of the storage layer.
_ALLOWED_ROLES = frozenset({"user", "assistant"})

ACTIVE_CHAT_VERSION = 2


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: int
    system: str

    @property
    def ref(self) -> str:
        # Audit-log identifier: pins the exact text, not just the version label.
        digest = sha256(self.system.encode()).hexdigest()[:8]
        return f"{self.name}.v{self.version}@{digest}"


def load_template(name: str, version: int) -> PromptTemplate:
    path = _TEMPLATE_DIR / name / f"v{version}.md"
    return PromptTemplate(name=name, version=version, system=path.read_text().strip())


CHAT_TEMPLATE = load_template("chat", ACTIVE_CHAT_VERSION)


@dataclass(frozen=True)
class AssembledPrompt:
    template: PromptTemplate
    messages: list[Message]


def assemble_chat_prompt(history: list[Message]) -> AssembledPrompt:
    for message in history:
        if message.get("role") not in _ALLOWED_ROLES:
            raise ValueError(f"disallowed role in conversation history: {message.get('role')!r}")
        if not isinstance(message.get("content"), str):
            raise ValueError("conversation content must be a string")
    return AssembledPrompt(template=CHAT_TEMPLATE, messages=list(history))
