from app.gateway import FakeGateway, Reply, ToolProposal
from app.tool_exec import _decide_tool


def test_fake_gateway_scripts_a_proposal_then_a_reply():
    g = FakeGateway(
        script=[
            ToolProposal("search_documents", {"query": "revenue"}, 0, 0),
            Reply("here is what I found", 0, 0),
        ]
    )
    first = g.propose("sys", [{"role": "user", "content": "what's our revenue?"}], tools=[])
    assert isinstance(first, ToolProposal)
    assert first.name == "search_documents"
    # The second leg of a tool turn uses complete(), which pops the queued Reply.
    second = g.complete("sys", [{"role": "user", "content": "..."}])
    assert isinstance(second, Reply)
    assert second.text == "here is what I found"


def test_fake_gateway_without_script_echoes():
    g = FakeGateway()
    reply = g.propose("sys", [{"role": "user", "content": "hi"}], tools=[])
    assert isinstance(reply, Reply)
    assert reply.text == "Echo: hi"


def test_unknown_tool_name_is_invalid_not_crash():
    outcome = _decide_tool(ToolProposal("no_such_tool", {}, 0, 0))
    assert outcome.status == "invalid"
    assert outcome.spec is None


def test_bad_args_are_invalid():
    # Oversized title fails validation -> invalid, no execution. (body is omitted on
    # purpose: it defaults to "", so title length is what trips validation here.)
    outcome = _decide_tool(ToolProposal("create_note", {"title": "x" * 500}, 0, 0))
    assert outcome.status == "invalid"


def test_tenant_injection_in_args_is_invalid():
    # A model-supplied org_id is an extra field -> validation fails -> invalid.
    outcome = _decide_tool(
        ToolProposal("create_note", {"title": "ok", "org_id": "evil"}, 0, 0)
    )
    assert outcome.status == "invalid"


def test_valid_proposal_decides_proposed_with_parsed_args():
    outcome = _decide_tool(ToolProposal("search_documents", {"query": "q"}, 0, 0))
    assert outcome.status == "proposed"
    assert outcome.spec is not None
    assert outcome.args.query == "q"
