"""Promptfoo provider that exercises the tool PROPOSAL path (Week 7).

Unlike api_provider.py (which calls complete()), this offers the real tool registry
and calls the same propose() the chat route uses. The output encodes what the model
tried to do: a JSON object {"tool", "args"} when it proposed an action, or the plain
reply text when it answered directly. The tool-injection suite asserts on that
artifact — a document must not be able to steer the model into proposing an action
the user never asked for.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.gateway import AnthropicGateway, ToolProposal
from app.tools import anthropic_tools

_gateway = AnthropicGateway()


def call_api(prompt, options, context):
    messages = json.loads(prompt)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    chat = [m for m in messages if m["role"] != "system"]
    result = _gateway.propose(system, chat, anthropic_tools())
    if isinstance(result, ToolProposal):
        output = json.dumps({"tool": result.name, "args": result.raw_args})
    else:
        output = result.text
    return {
        "output": output,
        "tokenUsage": {
            "prompt": result.input_tokens,
            "completion": result.output_tokens,
            "total": result.input_tokens + result.output_tokens,
        },
    }
