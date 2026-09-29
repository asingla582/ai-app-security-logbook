# System Card — AI App Security Logbook

**Version:** 1.3 · **Date:** 2026-09-29 · **Milestone:** v0.7
A one-page account of what this AI system is, what data it touches, what backs its
safety claims, and what remains at risk. Refreshed each milestone alongside the
[threat model](threat-model.md).

## What it is
A multi-tenant enterprise chat assistant. Users in an organization chat with a model;
every call is audited. Built as a public, week-by-week security engineering project.

## Model
- **Provider / model:** Anthropic, `claude-opus-4-8` (configurable via `CHAT_MODEL`).
- **Access:** server-side only, through a single gateway (`apps/api/app/gateway.py`).
  The browser never holds the model key. Tests and CI use a deterministic fake.
- **Sampling:** provider default (this model rejects a `temperature` parameter).
  Outputs are therefore non-deterministic; safety is measured as a rate over repeated
  runs, never asserted from a single run.
- **Training:** none. The project does not train or fine-tune; it calls a hosted model.

## Data it touches
- **Inputs:** user chat messages (may contain PII/secrets users paste); organization
  documents retrieved as context (Week 5 RAG), labeled `RETRIEVED` trust and fenced.
- **Actions:** two tools (Week 7) — `search_documents` (read) and `create_note` (write) —
  proposed by the model, authorized and executed by the application on the caller's
  connection. The model never chooses the tenant.
- **Outputs:** model responses rendered as sanitized markdown (Week 6): images never
  survive, and a link stays clickable only if its exact URL appeared in that request's
  retrieved context; everything else is de-fanged to inert text.
- **Retained:** raw messages and notes (org/user-scoped, RLS-isolated, deleted on
  conversation delete); a **redacted** audit copy of input/output in `model_calls` with
  context provenance and output-handling verdicts; a `proposed → decided → executed`
  trajectory per tool call in `tool_calls`. Both audit tables are server-write-only. No
  raw conversation content persists in the audit log.

## Controls
- Tenant isolation: Postgres RLS + independent app-layer authorization; tools run on the
  caller's RLS-scoped connection and take no tenant argument (confused-deputy is blocked
  by construction).
- Prompt integrity: versioned templates (`chat v5`); user input cannot occupy the system
  slot; retrieved and tool content enter only as fenced `RETRIEVED` chunks through one
  assembly path (structural, proven with no model in the loop).
- Agency limits: single-step tool pipeline (the answering call is offered no tools);
  Pydantic argument validation; per-org daily model-call ceiling and per-user tool-rate
  cap (HTTP 429 on breach), counted from the audit tables via SECURITY DEFINER functions.
- Output handling: server-side sanitizer strips images and de-fangs any URL not present
  in the request's retrieved context; the web client re-refuses images and raw HTML.
- Data protection: PII/secret redaction at the audit boundary, on input, output, and
  tool-call records.
- Supply chain / CI: lint, tests, RLS suite, Gitleaks, Trivy, and injection eval gates
  (direct, indirect, and tool-invocation) that double as model-drift detection.

## Safety evaluations
- **Direct prompt injection:** 20-case corpus, run through the app's gateway, gated in CI.
  20/20 on `chat v5`. Evidence: `evals/`, `evidence/week7/eval-run-direct-v5.txt`.
- **Indirect injection (RAG):** 12-case corpus of poisoned documents through the real
  assembly. 12/12 on `chat v5`. Evidence: `evidence/week7/eval-run-indirect-v5.txt`.
- **Tool-invocation injection:** 6-case corpus measuring whether a document can steer the
  model into proposing an action. 6/6. Evidence: `make eval-tools`, `evidence/week7/`.
- **Tool-calling red team:** full pipeline — injected writes produced no note; DoW ceiling
  engages; cross-tenant/tampering blocked. Evidence: `evidence/week7/`.
- **Structural guarantees:** asserted without the model (`test_prompting.py`,
  `test_provenance.py`, `test_tool_attacks.py`, `test_tools.py`).
- **Tenant isolation:** `tests/rls/`, `apps/api/tests/test_attacks.py`, `evidence/week1/`.
- **Redaction corpus:** `apps/api/tests/test_redaction.py` (catches and documented misses).

## Known residual risks
- Redaction misses obfuscated PII and free-form secrets (RR-W2-1/2) — accepted limit
  of signature matching; mitigated by a data-minimization stance.
- Authorized-but-steered write (RR-W7-1): a user-authorized `create_note` executes with
  no human approval, so injected content can influence what it records. The human-in-the-
  loop gate lands Week 8, with `create_note` as the designated tool.
- Fixed-window rate limits race under concurrency (RR-W7-2) — accepted at this scale.
- Document-hosted phishing links (RR-006): a forged document's own link is allowlist-legal
  and renders clickable; content-trust controls weighed at v0.8.
- Instruction-leak resistance is behavioral, not structural (RR-W3-3); the prompt holds
  no secrets by design.

See the [threat model](threat-model.md) for the full findings register and framework
mapping (OWASP LLM Top 10, NIST AI RMF, MITRE ATLAS).
