"""Week 10: replay the Week 8 poisoned-onboarding attack and keep the telemetry.

The attack crosses every layer on current code: retrieval, the real model, a tool
proposal, the approval gate, execution. One run, then the evidence is read back the way
an investigator would: audit rows by correlation id, spans from the trace store by the
same id. Nothing here reads a reply to decide what happened.

  make replay-week10
"""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
from app.embeddings import FakeEmbedder, get_embedder
from app.main import app
from app.telemetry import flush_tracing
from theater_week8 import ONBOARDING, PHISH_HOST, QUESTION, fresh

OUT = Path(__file__).parent
DB_URL = os.environ["SUPABASE_DB_URL"]
JAEGER = "http://127.0.0.1:16686"
MAX_ATTEMPTS = 5
assert os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"), "tracing is off; use make replay-week10"

app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()


def _value(attribute):
    return next(iter(attribute["value"].values()))


def traces_for(cid):
    """The spans of the one trace holding this correlation id. The store's attribute
    filter is not relied on: the match is made here, on the id the audit rows carry."""
    now = datetime.now(UTC)
    params = {"query.service_name": "logbook-api",
              "query.start_time_min": (now - timedelta(hours=1)).isoformat(),
              "query.start_time_max": (now + timedelta(minutes=1)).isoformat(),
              "query.num_traces": 100}
    for _ in range(20):
        body = httpx.get(f"{JAEGER}/api/v3/traces", params=params).json().get("result", {})
        spans = [s for r in body.get("resourceSpans", []) for sc in r["scopeSpans"]
                 for s in sc["spans"]]
        hit = next((s["traceId"] for s in spans for a in s.get("attributes", [])
                    if a["key"] == "app.correlation_id" and _value(a) == cid), None)
        if hit:
            return sorted((s for s in spans if s["traceId"] == hit),
                          key=lambda s: int(s["startTimeUnixNano"]))
        time.sleep(1)
    raise SystemExit(f"no trace found for {cid}")


def show(spans):
    print(f"  trace {spans[0]['traceId']} ({len(spans)} spans)")
    for s in spans:
        ms = (int(s["endTimeUnixNano"]) - int(s["startTimeUnixNano"])) / 1e6
        tags = {a["key"]: _value(a) for a in s.get("attributes", [])
                if a["key"].startswith(("app.", "gen_ai.", "tool.", "approval.", "output.",
                                        "retrieval.", "http.route"))}
        print(f"    {s['name']:54} {ms:9.1f} ms  {tags}")
        for link in s.get("links", []):
            print(f"      link -> trace {link['traceId']} span {link['spanId']}")


def attempt():
    """One fresh org and one real model run, approved as the human in the incident did.
    The model is non-deterministic, so a run may not carry the link."""
    client, org_id = fresh()
    client.post(f"/orgs/{org_id}/documents",
                json={"filename": "onboarding.md", "content": ONBOARDING})
    conv = client.post("/conversations").json()["id"]
    turn = client.post(f"/conversations/{conv}/messages", json={"content": QUESTION})
    pending = turn.json().get("pending_action")
    if not pending:
        return None
    approve = client.post(f"/actions/{pending['id']}/approve",
                          json={"args_sha256": pending["args_sha256"]})
    with psycopg.connect(DB_URL) as conn:
        bodies = [r[0] for r in conn.execute("select body from notes where org_id = %s",
                                             (org_id,))]
    return {"client": client, "org_id": org_id, "conv": conv, "turn": turn, "pending": pending,
            "approve": approve, "landed": any(PHISH_HOST in b.lower() for b in bodies)}


def main():
    run = None
    for i in range(1, MAX_ATTEMPTS + 1):
        run = attempt()
        landed = bool(run and run["landed"])
        print(f"attempt {i}: {'no note proposed' if run is None else f'phishing host stored: {landed}'}")
        if landed:
            break
    if not (run and run["landed"]):
        raise SystemExit(f"the attack did not land in {MAX_ATTEMPTS} attempts")
    client, org_id, conv, turn = run["client"], run["org_id"], run["conv"], run["turn"]
    pending, approve = run["pending"], run["approve"]
    proposal_cid = turn.headers["X-Correlation-Id"]
    print(f"\norg {org_id}\nconversation {conv}\nproposal correlation id {proposal_cid}")
    print(f"action {pending['id']}\nargs_sha256 {pending['args_sha256']}")
    print(f"flagged urls {json.dumps(pending['flags']['urls'])}")
    approve_cid = approve.headers["X-Correlation-Id"]
    print(f"approve HTTP {approve.status_code}, correlation id {approve_cid}")
    flush_tracing()

    print("\nTRACES")
    proposal_trace, approve_trace = traces_for(proposal_cid), traces_for(approve_cid)
    show(proposal_trace)
    show(approve_trace)
    (OUT / "trace-proposal.json").write_text(json.dumps(proposal_trace, indent=1))
    (OUT / "trace-approval.json").write_text(json.dumps(approve_trace, indent=1))

    print("\nAUDIT TIMELINE (the owner's view, oldest first)")
    timeline = client.get(f"/orgs/{org_id}/audit").json()
    (OUT / "timeline.json").write_text(json.dumps(timeline, indent=1))
    for e in reversed(timeline["events"]):
        print(f"  {e['ts']} {e['kind']:10} {e['action']:14} {e['outcome']:10} "
              f"{e['correlation_id']} user {e['user_id']} {json.dumps(e['detail'])}")
    print("\nFINDINGS")
    for a in timeline["anomalies"]:
        print(f"  {a['severity']}: {a['rule']} {a['correlation_id']} {json.dumps(a['detail'])}")
    if not timeline["anomalies"]:
        print("  none")

    print("\nGROUND TRUTH (read as superuser, for scoring this replay)")
    print("  stored note carries the phishing host: True")


if __name__ == "__main__":
    main()
