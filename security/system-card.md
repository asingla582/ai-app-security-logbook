# System Card: AI App Security Logbook

**Version:** 1.5 · **Date:** 2026-10-18 · **Milestone:** v0.8, measured in Week 9
A one-page account of what this AI system is, what data it touches, what backs its
safety claims, and what remains at risk. Refreshed each milestone alongside the
[threat model](threat-model.md).

## What it is
A multi-tenant enterprise chat assistant. Users in an organization chat with a model;
every call is audited. Built as a public, week-by-week security engineering project.

## Model
- **Provider / model:** Anthropic, `claude-opus-4-8` (configurable via `CHAT_MODEL`).
- **Access:** server-side only, through a single gateway (`apps/api/app/gateway.py`).
  The browser never holds the model key. The test suites use a deterministic fake;
  the scorecard's model rows call the real model.
- **Sampling:** provider default (this model rejects a `temperature` parameter).
  Outputs are therefore non-deterministic; safety is measured as a rate over repeated
  runs, never asserted from a single run.
- **Training:** none. The project does not train or fine-tune; it calls a hosted model.

## Data it touches
- **Inputs:** user chat messages (may contain PII/secrets users paste); organization
  documents retrieved as context (Week 5 RAG), labeled `RETRIEVED` trust and fenced.
- **Actions:** two tools (Week 7), `search_documents` (read) and `create_note` (write),
  proposed by the model, authorized and executed by the application on the caller's
  connection. The model never chooses the tenant. Since Week 8, `create_note` needs the
  requester's approval: the proposal is parked as a pending action, shown on an
  approval card, and executed only after the requester approves that exact stored row.
- **Outputs:** model responses rendered as sanitized markdown (Week 6): images never
  survive, and a link stays clickable only if its exact URL appeared in that request's
  retrieved context; everything else is de-fanged to inert text.
- **Retained:** raw messages and notes (org/user-scoped, RLS-isolated, deleted on
  conversation delete); a **redacted** audit copy of input/output in `model_calls` with
  context provenance and output-handling verdicts; a `proposed → decided → executed`
  trajectory per tool call in `tool_calls`. Both audit tables are server-write-only. No
  raw conversation content persists in the audit log. Pending actions
  (`pending_actions`) keep the unredacted args they will execute, readable only by
  the requester, immutable once written.

## Controls
- Tenant isolation: Postgres RLS + independent app-layer authorization; tools run on the
  caller's RLS-scoped connection and take no tenant argument (confused-deputy is blocked
  by construction).
- Prompt integrity: versioned templates (`chat v6`); user input cannot occupy the system
  slot; retrieved and tool content enter only as fenced `RETRIEVED` chunks through one
  assembly path (structural, proven with no model in the loop).
- Agency limits: single-step tool pipeline (the answering call is offered no tools);
  Pydantic argument validation; per-org daily model-call ceiling and per-user tool-rate
  cap (HTTP 429 on breach), counted from the audit tables via SECURITY DEFINER functions.
- Human approval (Week 8): tools marked `requires_approval` (today `create_note`) are
  never executed in the proposing turn. The database computes the args hash; an
  immutability trigger locks the row for every role; the claim is single-use,
  row-locked, expiring (15 minutes) and membership-checked; the approve request
  carries only the hash; the app executes the args read from the claimed row as the
  caller. The approval card shows the full body as plain text, every link with its
  source document (or "written by the assistant"), never as a clickable anchor, and
  text copied from a document.
- Output handling: server-side sanitizer strips images and de-fangs any URL not present
  in the request's retrieved context; the web client re-refuses images and raw HTML.
- Data protection: PII/secret redaction at the audit boundary, on input, output, and
  tool-call records.
- Supply chain / CI: Gitleaks and Trivy in CI. Since Week 9 one scorecard job runs
  ruff, pytest (API, RLS), vitest, Trivy and the three injection suites (direct,
  indirect, tool-invocation) and fails if any required row did not run. The model
  rows double as model-drift detection. Before Week 9 the CI eval job skipped in every
  run that had it (RR-W9-1), and v0.8 was released on a red build (RR-W9-2); both are fixed.
- Database grants (Week 9): no SECURITY DEFINER function is executable by `anon`
  (migration 0012), checked across the whole catalog by `tests/rls/test_definer_grants.py`;
  `record_model_call` requires membership of the org it records for.

## Safety evaluations
- **Scorecard (Week 9):** every suite below scored into twelve rows with thresholds
  fixed before the first run; a row that did not run fails. Recorded run 2 (repeat 10,
  `claude-opus-4-8`, `chat.v6@f0478b8c`, commit `662db3a`): PASS on every row. Run 1
  (commit `7f560a0`) failed Indirect injection at 22/23 because of a detector false
  positive (RR-W9-3) and is kept as recorded. Run 3 (2026-10-21, commit `088f1ae`, same
  settings) passed every row again and is the committed model evidence CI checks.
  Evidence: `evidence/week9/scorecard.md` (run 3), `scorecard-run2.md`,
  `scorecard-run1-fail.md`, `README.md`, `evidence/scorecard/model-latest.json`. Reproduce: `make scorecard`. Six model-row
  cases are judged by an LLM grader, not by code (listed in `evals/README.md`).
- **Direct prompt injection:** 20-case corpus (19 cases plus the pinned RR-W3-1
  canary), run through the app's gateway. Scorecard row 19/19 in every one of 10 runs on
  `chat v6`. Week 8 single run: 20/20, `evidence/week8/eval-run-direct-v6.txt`.
- **Indirect injection (RAG):** 12-case corpus of poisoned documents through the real
  assembly, plus the provenance and RAG-assembly tests. Scorecard row 23/23 in run 2.
  Week 8 single run: 12/12, `evidence/week8/eval-run-indirect-v6.txt`.
- **Tool-invocation injection:** 6-case corpus measuring whether a document can steer the
  model into proposing an action. Scorecard row 6/6 in every one of 10 runs. Week 8
  single run: 6/6, `evidence/week8/eval-run-tools-v6.txt`.
- **Trajectory (Week 9):** 7 tests read `tool_calls` and `pending_actions` from the
  database and assert the ordered sequence for read, approved, denied, expired,
  injected-`org_id` and wrong-hash flows, plus a sweep for executed writes without an
  executed approval. The model is the deterministic fake. Evidence:
  `apps/api/tests/test_trajectories.py`, scorecard row 7/7.
- **Tool-calling red team:** full pipeline: injected writes produced no note; DoW ceiling
  engages; cross-tenant/tampering blocked. Evidence: `evidence/week7/`.
- **Approval gate red team:** 12/12 held, 0 broke: parameter mutation, approval forgery,
  replay (including two concurrent claims and approval after expiry), an injected
  "pre-approved by IT" document (10/10 live runs with zero notes), and no executed
  write without an executed approval. Evidence: `evidence/week8/redteam-run.txt`.
- **Approval theater (the Week 8 break):** on card v1 (title plus 120 characters) a
  document's phishing link reached the approved, stored note while hidden from the card
  in 5/10 live runs by exact URL, 7/10 in any form. After card v2 and flag hardening
  the link landed in 6/10 and was flagged with its source document in all 6.
  Evidence: `evidence/week8/theater-run.txt`, `theater-run-v2.txt`, `walkthrough.md`.
- **Structural guarantees:** asserted without the model (`test_prompting.py`,
  `test_provenance.py`, `test_tool_attacks.py`, `test_tools.py`).
- **Tenant isolation:** `tests/rls/`, `apps/api/tests/test_attacks.py`, `evidence/week1/`.
- **Redaction corpus:** `apps/api/tests/test_redaction.py` (catches and documented misses).

## Known residual risks
- Redaction misses obfuscated PII and free-form secrets (RR-W2-1/2): accepted limit
  of signature matching; mitigated by a data-minimization stance.
- Authorized-but-steered write (RR-W7-1): **closed in Week 8.** Writes need the
  requester's approval, and what executes is the stored, hash-locked row.
- An informed human can still approve a bad action (RR-W8-1): HITL buys informed
  consent, not correctness. Mitigated by the card's provenance flags; candidate further
  controls are a four-eyes rule for flagged actions and URL reputation.
- Flag detection limits (RR-W8-2): obfuscated or glued link forms are not flagged, and
  scheme-less links only for hosts a retrieved document mentioned. The card always shows
  the full body.
- A requester can write a forged completion record for their own action (RR-W8-3):
  audit integrity only, no privilege gain.
- Definer functions from earlier migrations executable by `anon` (RR-W8-4): **closed
  in Week 9** by migration 0012 (revoke from `public` and `anon`, membership check in
  `record_model_call`) and a catalog-wide test. Side effect: an `anon` RLS read on six
  tables now errors (42501) instead of returning nothing; this fails closed and the app
  never reads as `anon`.
- `record_model_call` does not check that the conversation belongs to the org it
  records for (RR-W9-4, open, low): a user in two orgs could file an audit row under
  the wrong org. Audit accuracy only.
- The CI scorecard gate needs the author's `ANTHROPIC_API_KEY` repository secret.
  Without it the job fails on push and same-repo PRs, by design; fork PRs get a stated
  partial pass with the model rows not run.
- Eval detectors can be wrong: RR-W9-3 was the fifth time a detector here scored a
  mention instead of the artifact. The scorecard reports what each detector says;
  transcripts are still read by hand.
- An approved action can stay `approved` if the database fails between the claim and
  completion: a stated limit. It fails closed (nothing runs twice), and no sweep for
  such rows exists yet.
- Fixed-window rate limits race under concurrency (RR-W7-2): accepted at this scale.
- Document-hosted phishing links (RR-006): a forged document's own link is allowlist-legal
  and renders clickable in chat replies. **Accepted at v0.8** as a content-trust problem;
  links are attributed to their source, and the approval card applies the same idea to
  writes. Candidate controls: URL reputation, sensitivity-scoped link policy,
  non-clickable document links.
- Instruction-leak resistance is behavioral, not structural (RR-W3-3); the prompt holds
  no secrets by design.

See the [threat model](threat-model.md) for the full findings register and framework
mapping (OWASP LLM Top 10, NIST AI RMF, MITRE ATLAS).
