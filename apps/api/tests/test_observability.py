"""Week 10: spans tell the story without carrying its content, a client cannot steer
the trace or the correlation id, and approvals leave a linked, recorded decision."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.db import db_for_user
from app.gateway import FakeGateway, Reply, ToolProposal, get_gateway
from app.limits import RateLimited
from app.main import app

from .conftest import DB_URL

CANARY = "CANARY-7719-SIGMA"
exporter = InMemorySpanExporter()
provider = trace.get_tracer_provider()
if not isinstance(provider, TracerProvider):
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
provider.add_span_processor(SimpleSpanProcessor(exporter))


@pytest.fixture(autouse=True)
def _clear():
    exporter.clear()
    yield


def _names():
    return {s.name for s in exporter.get_finished_spans()}


def _script(*steps):
    app.dependency_overrides[get_gateway] = lambda: FakeGateway(list(steps))


def _org(client):
    return client.post("/orgs", json={"name": "Org"}).json()["id"]


def _turn_to_pending(client, body=f"secret {CANARY}"):
    conv = client.post("/conversations").json()["id"]
    _script(ToolProposal("create_note", {"title": CANARY, "body": body}, 1, 1),
            Reply(f"Parked {CANARY}", 1, 1))
    r = client.post(f"/conversations/{conv}/messages", json={"content": f"note {CANARY}"})
    assert r.status_code == 201
    return r


def test_turn_spans_cover_the_path_and_carry_no_content(alice_client):
    _org(alice_client)
    r = _turn_to_pending(alice_client)
    assert {"retrieval.search", "model.propose", "tool.decide", "model.complete",
            "output.sanitize"} <= _names()
    root = next(s for s in exporter.get_finished_spans() if s.name.endswith("/messages"))
    assert root.attributes["app.correlation_id"] == r.headers["X-Correlation-Id"]
    assert root.attributes["http.route"] == "/conversations/{conversation_id}/messages"
    for span in exporter.get_finished_spans():
        assert CANARY not in str(dict(span.attributes)), span.name
        assert CANARY not in span.name


def test_one_trace_per_request(alice_client):
    _org(alice_client)
    _turn_to_pending(alice_client)
    spans = [s for s in exporter.get_finished_spans()
             if s.name in {"retrieval.search", "model.propose", "tool.decide"}]
    assert len({s.context.trace_id for s in spans}) == 1


def test_client_traceparent_is_a_link_never_a_parent(alice_client):
    claimed = "ab" * 16
    alice_client.get("/me", headers={"traceparent": f"00-{claimed}-{'cd' * 8}-01"})
    root = next(s for s in exporter.get_finished_spans() if s.name.startswith("HTTP GET"))
    assert root.parent is None
    assert format(root.context.trace_id, "032x") != claimed
    assert [format(link.context.trace_id, "032x") for link in root.links] == [claimed]


def test_malformed_traceparent_is_dropped(alice_client):
    r = alice_client.get("/me", headers={"traceparent": "garbage"})
    assert r.status_code == 200
    root = next(s for s in exporter.get_finished_spans() if s.name.startswith("HTTP GET"))
    assert list(root.links) == []


def test_approval_links_to_proposal_and_records_a_decision(alice_client):
    _org(alice_client)
    pending = _turn_to_pending(alice_client).json()["pending_action"]
    proposal = next(s for s in exporter.get_finished_spans() if s.name == "tool.decide")
    r = alice_client.post(f"/actions/{pending['id']}/approve",
                          json={"args_sha256": pending["args_sha256"]})
    assert r.status_code == 200
    decide = next(s for s in exporter.get_finished_spans() if s.name == "approval.decide")
    assert decide.context.trace_id != proposal.context.trace_id
    assert [link.context.trace_id for link in decide.links] == [proposal.context.trace_id]
    assert decide.attributes["approval.outcome"] == "executed"
    with psycopg.connect(DB_URL) as conn:
        row = conn.execute(
            "select correlation_id, outcome from audit_events where subject_id = %s "
            "and action = 'approve_action'", (pending["id"],)).fetchone()
    assert row == (r.headers["X-Correlation-Id"], "executed")


def test_hash_mismatch_is_recorded(alice_client):
    _org(alice_client)
    pending = _turn_to_pending(alice_client).json()["pending_action"]
    r = alice_client.post(f"/actions/{pending['id']}/approve", json={"args_sha256": "wrong"})
    assert r.status_code == 409
    with psycopg.connect(DB_URL) as conn:
        assert conn.execute(
            "select outcome from audit_events where subject_id = %s", (pending["id"],)
        ).fetchone() == ("hash_mismatch",)


def test_revoked_member_approval_attempt_is_still_recorded(alice_client):
    org_id = _org(alice_client)
    pending = _turn_to_pending(alice_client).json()["pending_action"]
    with psycopg.connect(DB_URL) as conn:
        conn.execute("delete from memberships where org_id = %s", (org_id,))
    r = alice_client.post(f"/actions/{pending['id']}/approve",
                          json={"args_sha256": pending["args_sha256"]})
    assert r.status_code == 404
    with psycopg.connect(DB_URL) as conn:
        assert conn.execute(
            "select org_id::text, outcome from audit_events where subject_id = %s",
            (pending["id"],)).fetchone() == (org_id, "not_found")


def test_approving_a_document_link_is_a_finding(alice_client):
    # The Week 10 replay: flagged, approved and stored, and the rules said nothing.
    org_id = _org(alice_client)
    alice_client.post(f"/orgs/{org_id}/documents", json={
        "filename": "onboarding.md", "content": "Sign in at https://sso-acme-verify.example/login"})
    pending = _turn_to_pending(alice_client, body="go to sso-acme-verify.example/login")
    action = pending.json()["pending_action"]
    url = f"/orgs/{org_id}/audit"
    assert alice_client.get(url).json()["anomalies"] == []
    alice_client.post(f"/actions/{action['id']}/approve",
                      json={"args_sha256": action["args_sha256"]})
    found = alice_client.get(url).json()["anomalies"]
    assert [(a["rule"], a["subject_id"]) for a in found] == [
        ("approved_document_link", action["id"])]
    assert found[0]["detail"]["hosts"] == ["sso-acme-verify.example"]


def test_a_failed_write_leaves_only_the_error_type_on_spans(alice_client, monkeypatch):
    # A database error can quote the row. The SDK default would store that message.
    _org(alice_client)
    conv = alice_client.post("/conversations").json()["id"]
    _script(ToolProposal("create_note", {"title": CANARY, "body": "b"}, 1, 1))

    def boom(*_args):
        raise psycopg.errors.InvalidTextRepresentation(f"JSON data: {CANARY}")

    monkeypatch.setattr("app.tool_exec._record_proposal", boom)
    client = TestClient(app, headers=alice_client.headers, raise_server_exceptions=False)
    r = client.post(f"/conversations/{conv}/messages", json={"content": "go"})
    assert r.status_code == 500
    spans = exporter.get_finished_spans()
    decide = next(s for s in spans if s.name == "tool.decide")
    root = next(s for s in spans if s.name.endswith("/messages"))
    assert decide.attributes["error.type"] == "InvalidTextRepresentation"
    assert root.attributes["http.response.status_code"] == 500
    assert root.status.status_code is StatusCode.ERROR
    for span in spans:
        assert CANARY not in str(span.events), span.name
        assert CANARY not in str(span.status.description), span.name


def test_unregistered_tool_name_stays_off_spans(alice_client):
    _org(alice_client)
    conv = alice_client.post("/conversations").json()["id"]
    _script(ToolProposal(CANARY, {}, 1, 1), Reply("plain", 1, 1))
    assert alice_client.post(f"/conversations/{conv}/messages",
                             json={"content": "go"}).status_code == 201
    for span in exporter.get_finished_spans():
        assert CANARY not in str(dict(span.attributes)), span.name


def test_lone_surrogate_in_args_is_recorded_not_a_500(alice_client):
    _org(alice_client)
    conv = alice_client.post("/conversations").json()["id"]
    _script(ToolProposal("create_note", {"title": "a\ud800", "body": "b"}, 1, 1),
            Reply("plain", 1, 1))
    r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "go"})
    assert r.status_code == 201


def test_user_is_on_every_authenticated_root_span(alice_client):
    me = alice_client.get("/me")
    root = next(s for s in exporter.get_finished_spans() if s.name.startswith("HTTP GET"))
    assert root.attributes["app.user_id"] == me.json()["id"]


@pytest.mark.parametrize("path", ["approve", "deny"])
def test_nul_in_action_id_is_404_not_500(alice_client, path):
    body = {"json": {"args_sha256": "x"}} if path == "approve" else {}
    assert alice_client.post(f"/actions/%00/{path}", **body).status_code == 404


def test_bad_correlation_filter_is_rejected(alice_client):
    org_id = _org(alice_client)
    assert alice_client.get(f"/orgs/{org_id}/audit",
                            params={"correlation_id": "\x00"}).status_code == 422


def test_revoked_member_at_budget_gets_429_and_a_record(alice_client, monkeypatch):
    org_id = _org(alice_client)
    conv = alice_client.post("/conversations").json()["id"]
    with psycopg.connect(DB_URL) as conn:
        conn.execute("delete from memberships where org_id = %s", (org_id,))
    monkeypatch.setattr("app.routes_chat.check_org_model_budget",
                        lambda *_: (_ for _ in ()).throw(RateLimited("org_budget", "later")))
    r = alice_client.post(f"/conversations/{conv}/messages", json={"content": "go"})
    assert r.status_code == 429
    with psycopg.connect(DB_URL) as conn:
        assert conn.execute("select action, outcome from audit_events where subject_id = %s",
                            (conv,)).fetchone() == ("model_budget", "refused")


def test_rollback_before_first_commit_stays_impersonated(alice_client):
    with db_for_user(alice_client.get("/me").json()["id"]) as conn:
        conn.rollback()
        assert conn.execute("select current_user").fetchone()[0] == "authenticated"


def test_timeline_route_owner_sees_a_turn_and_outsider_gets_404(alice_client, bob_setup):
    org_a = _org(alice_client)
    r = _turn_to_pending(alice_client)
    cid = r.headers["X-Correlation-Id"]
    timeline = alice_client.get(f"/orgs/{org_a}/audit", params={"correlation_id": cid})
    assert timeline.status_code == 200
    assert {e["kind"] for e in timeline.json()["events"]} == {
        "model_call", "tool_call", "approval"}
    assert CANARY not in timeline.text
    # flags.urls stores scheme-less links too, so hosts come from its parsed host field.
    alice_client.post(f"/orgs/{org_a}/documents", json={
        "filename": "onboarding.md", "content": "Sign in at https://sso-acme-verify.example/login"})
    pending = _turn_to_pending(alice_client, body="go to sso-acme-verify.example/login")
    action_cid = pending.headers["X-Correlation-Id"]
    rows = alice_client.get(f"/orgs/{org_a}/audit", params={"correlation_id": action_cid}).json()
    approval = next(e for e in rows["events"] if e["kind"] == "approval")
    assert approval["detail"]["flagged_hosts"] == ["sso-acme-verify.example"]
    assert "/login" not in str(approval["detail"])
    assert bob_setup["client"].get(f"/orgs/{org_a}/audit").status_code == 404
    assert alice_client.get(f"/orgs/{uuid.uuid4()}/audit").status_code == 404
