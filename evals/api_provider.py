"""Promptfoo provider that calls the application's own model gateway.

The eval drives the same AnthropicGateway class production uses (same model and
token cap from apps/api config), so a passing report is evidence about the
shipped call path, not about promptfoo's reimplementation of it.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.gateway import AnthropicGateway

_gateway = AnthropicGateway()


def call_api(prompt, options, context):
    messages = json.loads(prompt)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    chat = [m for m in messages if m["role"] != "system"]
    reply = _gateway.complete(system, chat)
    return {
        "output": reply.text,
        "tokenUsage": {
            "prompt": reply.input_tokens,
            "completion": reply.output_tokens,
            "total": reply.input_tokens + reply.output_tokens,
        },
    }
