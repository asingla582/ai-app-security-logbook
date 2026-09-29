# Week 7 evidence — secure tool calling

The assistant gained two actions this week: `search_documents` (read) and
`create_note` (write). The question is not "does the model behave" but "can the
*application* be made to do something it should not" — take an action across
tenants, take an action a document ordered, or be run into the ground by a flood.
Everything here scores the **artifact** (a row in a table, an HTTP 429), never a
phrase in a reply.

Maps to OWASP LLM06 (Excessive Agency) and LLM10 (Unbounded Consumption).

## What was measured

| Attack class | Layer | Result | Where proven |
|---|---|---|---|
| Cross-tenant confused deputy | app | **structurally impossible** | `apps/api/tests/test_tool_attacks.py` |
| Parameter tampering (extra/oversized args) | app | **rejected at validation** | `apps/api/tests/test_tool_attacks.py`, `test_tools.py` |
| Injection-driven tool invocation | model+prompt | **held 6/6** | `make eval-tools` (`promptfoo-report-tools.json`) |
| Injection-driven write (full pipeline) | model+app | **held 0/3, no note created** | `redteam-run.txt` (A1, A2) |
| Denial-of-wallet flood | app | **ceiling engages (429)** after a fix | `redteam-run.txt` (B) |

## The controls that hold by construction

Cross-tenant and parameter tampering are not model-dependent, so they are proven
deterministically rather than re-run against the live model:

- **The model never chooses the tenant.** Tool argument models carry no `org_id`
  or `user_id`; both are `extra="forbid"`, so a proposal that names another org is
  rejected at validation (`invalid`) before anything executes. Org comes from the
  conversation row, identity from the session. A registry-load assertion fails the
  app if a future tool forgets `extra="forbid"`.
- **Tools run as the caller.** Execution is on the caller's RLS-scoped connection,
  so even a hypothetical bypass of the app check still meets row-level security.

## The model layer holds against document-driven actions

`make eval-tools` runs the injection corpus through the real assembly (v5 prompt,
nonce fences, provenance labels) and the real `propose()` with the tool registry
offered. The model was asked, via document content, to take a write action the
user never requested — loud orders, helpful-sounding tips, forged application
framing, and an exfil-via-note attempt. **6/6 passed:** the model proposed a write
only when the *user* asked, and refused every document-driven one, usually calling
out the embedded instruction.

Detector note (the recurring lesson, hit again here): one assertion initially
flagged an internal codename appearing in a plain-text summary to the authorized
user as a break. It is not — the user already has access to that document, the
reply is not an exfiltration channel, and no write occurred. The assertion was
corrected to score the artifact (a `create_note` proposal that would *persist* the
codename), not the mention. Score the deliverable, never the mention.

Live full-pipeline confirmation (`redteam-run.txt`, A1/A2): across runs, an
injected "create a note with these credentials" never produced a note
(`tool_calls=[]`, notes table empty). Held 0/3.

## The denial-of-wallet control — and the bug the red team caught

The first live run of the flood attack (attack B) showed **the ceiling never
tripped**: six rapid messages all returned 201. Investigation found the cause —
`check_org_model_budget` and `check_user_tool_rate` counted from `model_calls` /
`tool_calls`, but those audit tables are not readable by the `authenticated` role
(RLS with no policy), so the `count(*)` on the caller's connection **always
returned zero**. The rate limits were silently no-ops in production.

The unit tests had used a mocked connection, and the 429 route tests had
monkeypatched the check to raise — so neither exercised the real count. Only the
live pipeline run surfaced it. This is the week's demonstrated break: a control
that looked correct in isolation did nothing in place.

**Fix:** migration `0009_rate_limit_counters.sql` adds SECURITY DEFINER counting
functions (`count_org_model_calls_1d`, `count_user_tool_calls_1m`) that read the
audit tables with the right privileges, and `limits.py` calls them. A regression
test (`test_org_model_budget_engages_via_real_count`) now exercises the real count
path, not a mock. After the fix, the flood trips the ceiling: requests past the
budget return HTTP 429 (`redteam-run.txt`, attack B — 201×4 then 429×2 at a demo
cap of 4/day; the mechanism is identical at the 200/day default).

## Stated residual → Week 8

`create_note` is a write that executes immediately with **no human approval**.
When a write is proposed — by a legitimate user request, or in the rare event the
model is steered into one — nothing holds it for review. That gap is the concrete
motivation for Week 8's human-in-the-loop approval queue, with `create_note` as
the designated high-risk tool. The authorization, validation, logging, and rate
limits shipped this week are the floor; the approval gate is the next layer.

## Reproduce

```
make eval-tools          # model-layer injection corpus (needs ANTHROPIC_API_KEY)
REDTEAM_RUNS=10 make redteam-week7   # full-pipeline red team + DoW ceiling
make test                # app + rls tests, incl. the real-count regression
```

Files: `promptfoo-report-tools.json` / `eval-run-tools.txt` (model layer),
`redteam-run.txt` (full pipeline), `redteam_week7.py` (the harness),
`eval-run-direct-v5.txt` / `eval-run-indirect-v5.txt` (v5 regression: direct 20/20,
indirect 12/12 — no regression from the tool paragraph).
