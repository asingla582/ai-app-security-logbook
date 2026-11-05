"""Week 10 break: the audit trail can be rewritten by the person it describes.

The walkthrough this week reconstructs an incident from the audit rows, so those
rows have to be evidence. This attacks them with what a signed-in user already
holds in the browser: their own token and the public anon key, calling the
database API directly and never touching our API.

  1. COVER-UP   - an executed tool call is rewritten to read "denied".
  2. FABRICATE  - a model call that never happened is added to the trail.
  3. PHANTOM    - an approved action is marked executed without running.
  4. POISON     - another org's user writes into the victim's correlation id, so a
                  lookup by that id returns both tenants' rows as one incident.
  5. HEADER     - the same, by sending X-Correlation-Id to our API. Added after run 1:
                  it is scored in the after-fix run only.

  set -a; . ./.env; set +a; cd apps/api && . .venv/bin/activate
  python ../../evidence/week10/forge_audit.py

Detector rule: score the stored rows, read back as superuser, never the HTTP code.
"""

import os
import uuid

import httpx
import psycopg
from app.main import app
from fastapi.testclient import TestClient

URL = os.environ["SUPABASE_URL"]
ANON = os.environ["SUPABASE_ANON_KEY"]
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
DB_URL = os.environ["SUPABASE_DB_URL"]
PASSWORD = "Password123!"


def user(prefix):
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    r = httpx.post(f"{URL}/auth/v1/admin/users",
                   headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"},
                   json={"email": email, "password": PASSWORD, "email_confirm": True})
    r.raise_for_status()
    uid = r.json()["id"]
    t = httpx.post(f"{URL}/auth/v1/token?grant_type=password", headers={"apikey": ANON},
                   json={"email": email, "password": PASSWORD})
    t.raise_for_status()
    return uid, t.json()["access_token"]


def rpc(token, fn, **args):
    """Exactly what a browser could send: anon key, own token, direct to the database API."""
    r = httpx.post(f"{URL}/rest/v1/rpc/{fn}", json=args,
                   headers={"apikey": ANON, "Authorization": f"Bearer {token}"})
    print(f"    POST /rest/v1/rpc/{fn} -> {r.status_code}")
    return r


def seed(db, uid, name):
    """The honest state the app would have left behind, written as superuser."""
    org = db.execute("insert into organizations (name) values (%s) returning id", (name,)).fetchone()[0]
    db.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'owner')", (org, uid))
    conv = db.execute("insert into conversations (org_id, user_id) values (%s, %s) returning id",
                      (org, uid)).fetchone()[0]
    return str(org), str(conv)


def main():
    attacker, token = user("attacker")
    victim, _ = user("victim")
    db = psycopg.connect(DB_URL, autocommit=True)
    org, conv = seed(db, attacker, "Attacker org")
    v_org, v_conv = seed(db, victim, "Victim org")
    results = {}

    print("\n1. COVER-UP: rewrite an executed tool call to read denied")
    tc = db.execute(
        "insert into tool_calls (correlation_id, user_id, org_id, conversation_id, tool_name, args,"
        " status, result_summary, decided_at, executed_at) values (%s,%s,%s,%s,'search_documents','{}',"
        "'executed','note created',now(),now()) returning id",
        (str(uuid.uuid4()), attacker, org, conv)).fetchone()[0]
    rpc(token, "finalize_tool_call", p_id=str(tc), p_status="denied",
        p_result_summary="refused by policy", p_executed=False)
    row = db.execute("select status, result_summary from tool_calls where id=%s", (tc,)).fetchone()
    print(f"    stored row: {row}")
    results["cover-up"] = row[0] == "denied"

    print("\n2. FABRICATE: add a model call that never happened")
    fake = f"forged-{uuid.uuid4().hex[:8]}"
    rpc(token, "record_model_call", p_correlation_id=fake, p_org_id=org, p_conversation_id=conv,
        p_model="claude-opus-4-8", p_prompt="chat.v6", p_redacted_input="what is our leave policy?",
        p_redacted_output="24 days.", p_input_tokens=10, p_output_tokens=3, p_sources=[],
        p_context_provenance={}, p_output_handling={})
    n = db.execute("select count(*) from model_calls where correlation_id=%s", (fake,)).fetchone()[0]
    print(f"    rows stored under {fake}: {n}")
    results["fabricate"] = n == 1

    print("\n3. PHANTOM: mark an approved action executed without running it")
    tc2 = db.execute(
        "insert into tool_calls (correlation_id, user_id, org_id, conversation_id, tool_name, args, status)"
        " values (%s,%s,%s,%s,'create_note','{}','pending_approval') returning id",
        (str(uuid.uuid4()), attacker, org, conv)).fetchone()[0]
    action, digest = db.execute(
        "insert into pending_actions (tool_call_id, correlation_id, requester, org_id,"
        " conversation_id, tool_name, args, args_sha256, status, expires_at)"
        " select %s, %s, %s, %s, %s, 'create_note', a,"
        " encode(sha256(convert_to(a::text, 'UTF8')), 'hex'), 'pending',"
        " now() + interval '15 minutes'"
        " from (select '{\"title\":\"t\",\"body\":\"b\"}'::jsonb a) x"
        " returning id, args_sha256", (tc2, str(uuid.uuid4()), attacker, org, conv)).fetchone()
    rpc(token, "claim_pending_action", p_id=str(action), p_args_sha256=digest)
    rpc(token, "complete_pending_action", p_id=str(action), p_executed=True,
        p_result_ref=str(uuid.uuid4()), p_result_summary="note created")
    status, ref = db.execute("select status, result_ref from pending_actions where id=%s", (action,)).fetchone()
    note = db.execute("select count(*) from notes where id=%s", (ref,)).fetchone()[0] if ref else 0
    print(f"    stored status: {status}; notes behind result_ref: {note}")
    results["phantom"] = status == "executed" and note == 0

    print("\n4. POISON: another org writes into the victim's correlation id")
    shared = str(uuid.uuid4())
    db.execute("insert into model_calls (correlation_id, user_id, org_id, conversation_id, model, prompt,"
               " redacted_input, redacted_output) values (%s,%s,%s,%s,'m','chat.v6','real','real')",
               (shared, victim, v_org, v_conv))
    rpc(token, "record_model_call", p_correlation_id=shared, p_org_id=org, p_conversation_id=conv,
        p_model="claude-opus-4-8", p_prompt="chat.v6", p_redacted_input="planted",
        p_redacted_output="planted", p_input_tokens=1, p_output_tokens=1, p_sources=[],
        p_context_provenance={}, p_output_handling={})
    orgs = db.execute("select count(distinct org_id) from model_calls where correlation_id=%s",
                      (shared,)).fetchone()[0]
    print(f"    orgs under one correlation id: {orgs}")
    results["poison"] = orgs == 2

    print("\n5. HEADER: pick the correlation id through our own API")
    r = TestClient(app).get("/health", headers={"X-Correlation-Id": shared})
    print(f"    sent {shared}, server used {r.headers['X-Correlation-Id']}")
    results["header"] = r.headers["X-Correlation-Id"] == shared

    landed = sum(results.values())
    print("\nRESULTS")
    for k, v in results.items():
        print(f"  {k:10} {'LANDED' if v else 'blocked'}")
    print(f"\n{landed}/{len(results)} forgeries landed")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
