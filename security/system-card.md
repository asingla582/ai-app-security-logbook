# System Card — AI App Security Logbook

**Version:** 1.0 · **Date:** 2026-09-06 · **Milestone:** v0.4
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
- **Inputs:** user chat messages (may contain PII/secrets users paste).
- **Outputs:** model responses, shown to the user as text (no rendering of links/images
  as active content yet).
- **Retained:** raw messages (user-scoped, RLS-isolated, deleted on conversation delete);
  a **redacted** audit copy of input/output in `model_calls`, plus metadata (model,
  prompt template ref `chat.vN@<hash>`, token counts, correlation ID). No raw
  conversation content persists in the audit log.

## Controls
- Tenant isolation: Postgres RLS + independent app-layer authorization.
- Prompt integrity: versioned templates; user input cannot occupy the system slot
  (structural, proven with no model in the loop).
- Data protection: PII/secret redaction at the audit boundary, on both input and output.
- Supply chain / CI: lint, tests, RLS suite, Gitleaks, Trivy, and a direct-injection
  eval gate (also serves as model-drift detection across releases).

## Safety evaluations
- **Direct prompt injection:** 20-case corpus (override / role hijack / instruction
  leak), run through the app's own gateway, gated in CI. All attacks blocked across 10
  runs on the shipped prompt. Evidence: `evidence/week3/`, `evals/`.
- **Structural guarantees:** asserted without the model (`apps/api/tests/test_prompting.py`).
- **Tenant isolation:** `tests/rls/`, `apps/api/tests/test_attacks.py`, `evidence/week1/`.
- **Redaction corpus:** `apps/api/tests/test_redaction.py` (asserts both catches and
  documented misses).

## Known residual risks
- Redaction misses obfuscated PII and free-form secrets (RR-W2-1/2) — accepted limit
  of signature matching; mitigated by a data-minimization stance.
- Denial-of-wallet: no rate limiting yet (→ Week 7).
- Output-channel exfiltration (rendered links/images) not yet defended (→ Week 6).
- Instruction-leak resistance is behavioral, not structural (RR-W3-3); the prompt holds
  no secrets by design.

See the [threat model](threat-model.md) for the full findings register and framework
mapping (OWASP LLM Top 10, NIST AI RMF, MITRE ATLAS).
