# AI App Security Logbook

**Building a secure AI app in public, one feature and one attack at a time.**

> I'm building an AI assistant, then trying to break into it every week. Here's what happens.

AI App Security Logbook is a public engineering journal documenting the design, construction, attack, and defense of a production-style enterprise AI application. Every architectural decision is recorded, every security control is tested, every feature is attacked before release, and every claim is backed by evidence.

## What this is

A 12-week build of an enterprise AI assistant (multi-tenant auth, chat, document retrieval, tool calling) where each week follows the same cycle:

1. **Build** one vertical slice that works end to end.
2. **Attack** it, and show the attacks working before any defense exists.
3. **Defend** it, and show which attacks now fail and which still don't.
4. **Ship** the evidence: eval results, threat-model updates, and architecture decision records.

**Stack:** Next.js · FastAPI · Supabase (Postgres, Row Level Security, pgvector) · Promptfoo for security evaluations. No agent framework: authorization and trust boundaries are implemented directly, where they can be read and audited.

## The honesty promise

- Every security control is attacked before it ships. Results are committed, pass or fail.
- Residual risk is documented every release. If a defense is probabilistic, it says so.
- Prompt injection is treated as unsolved, because it is. The goal is raising the cost of attack and proving where the walls hold, not claiming walls can't be climbed.
- If the story and the engineering ever disagree, the engineering wins and the story gets fixed.

## Roadmap

| Phase | Weeks | Focus |
|---|---|---|
| Trust Foundation | 1–4 | Auth, multi-tenancy, RLS, audit logging, PII redaction, direct prompt injection (v0.4) |
| Retrieval & Injection | 5–8 | Secure RAG, indirect prompt injection, output handling, tool calling, human approval (v0.8) |
| Evidence & Production | 9–12 | Consolidated eval suite, observability, incident walkthrough, hardening (v1.0) |

Week 1 ships zero AI features on purpose: the boundaries between tenants exist before any model does.

## Planned, deliberately scoped out of v1

These are not missing features; they are scoped out so that what ships is finished and attacked, not half-built:

- Long-term memory with isolation and expiry
- Multi-step agent workflows with budgets and failure recovery
- Provider abstraction and cross-provider evaluation
- Incident replay / trace explorer UI
- Sandboxed shell execution, excluded intentionally: an unhardened shell tool is the most attackable surface an AI app can ship, and it deserves its own threat model before it exists anywhere

## Run it locally

Prerequisites: Docker (running), Node 20+ (for `npx`), and Python 3.11+.
Everything runs against a local Supabase stack; no accounts or secrets required.

```
make up      # bootstrap + seed + run: Supabase, .env, API venv, two demo orgs, app
make test    # run every test suite
make attack  # run the cross-tenant attack suite and capture evidence
```

`make up` is self-contained on a fresh clone: it starts the local Supabase stack,
writes the local keys into `.env`, builds the API virtualenv, seeds the demo
tenants, and launches the app. Chatting against the real model is optional; add
`ANTHROPIC_API_KEY` to `.env` for that. Without it, the app and the full test suite
still run against a deterministic fake.

Then open http://localhost:3000 and sign in as `alice@example.com` /
`Password123!`. You will see only Org A. Bob's org and notes are unreachable, by
the database and by the API.

## Status

**Week 1 shipped — Trust Foundation.** Auth, organizations, and notes, with tenant
isolation enforced by Postgres Row Level Security and a defense-in-depth
authorization layer in the API. A five-part cross-tenant attack suite runs in CI;
every attempt is denied and logged. See [`evidence/week1/`](evidence/week1/).

**Week 2 shipped — First AI Slice.** Chat over a thin model gateway, a structured
audit log of every model call (redacted at the application boundary, correlated by
request ID), and conversation lifecycle with real deletion. Attacked live; the
redaction findings and fixes are in [`evidence/week2/`](evidence/week2/).

**Week 3 shipped — Instruction Security.** Versioned prompt templates with the
prompt hash recorded in the audit log, and structural separation so user input can
never occupy the system slot (proven by tests that never call the model). A
Promptfoo direct-injection suite (override, role hijack, instruction leak) measures
the model-in-the-loop behavior. Overrides and role hijacks were blocked on all 10
runs; the instruction-leak result exposed a bug in the eval itself (a too-strict
detector scored correct refusals as leaks), which was found by reading the
transcripts, fixed to a coverage measure, and verified by re-scoring the saved runs
(0 real leaks, while still catching a genuine baseline dump). One string-puzzle case
(payload splitting) is kept as a documented canary: harmless in this app today, but
a preview of why model output can't be trusted once it is rendered or fed to a tool
(Week 6). Measured results, the correction, and the honest residuals are in
[`evidence/week3/`](evidence/week3/).

**Week 4 shipped — v0.4 "Trust Foundation."** Hardening only, no new features. The
open findings from weeks 1-3 are triaged in a versioned [threat model](security/threat-model.md)
(closed-loop framing, mapped to OWASP LLM Top 10, NIST AI RMF, and MITRE ATLAS, with
an honest maturity self-assessment) and summarized in a [system card](security/system-card.md).
Local setup now works from a fresh clone: `make up` starts Supabase, fills `.env`,
builds the API venv, seeds demo tenants, and runs the app.

**Week 5 shipped — Secure RAG.** Document upload with sensitivity labels at ingest,
chunking + pgvector embeddings, and retrieval authorized by the database before the
model sees anything — RLS plus an explicit org filter, proven with no model in the
loop including a crafted-vector probe. Every model call records which documents fed
it (data lineage in the audit log). Attacked live both ways: cross-tenant retrieval
held; indirect content poisoning broke cleanly (a forged help page's phishing link,
relayed with a citation), recorded as the open finding that owns Week 6. See
[`evidence/week5/`](evidence/week5/).

**Week 6 shipped — Indirect Injection & Output Handling.** Every piece of context
now carries a server-assigned trust tier (SYSTEM / USER / RETRIEVED); retrieved text
enters the prompt only inside per-request nonce fences a document can neither know
nor forge. On the way out — the EchoLeak lesson — model output is sanitized before
storage: images never survive, and a link stays clickable only if its exact URL
already appears in the retrieved sources, with every verdict recorded in the audit
log. Measured over 10 runs per attack: constructed-URL exfiltration (canary in an
outbound URL) went 8/10 → 0/10, payload splitting 10/10 → 0/10; the honest residual
(a document's own phishing link is allowlist-legal and still renders, 10/10) is
RR-006 in the threat model. A third eval-scorer bug was caught and the detector
rule codified: score what a renderer would activate, not what words appear. See
[`evidence/week6/`](evidence/week6/).

Next: Week 7 (secure tool calling) — a small tool registry with application-side
authorization per call, input validation, execution logging, and rate limits; tool
output enters prompts under the same provenance labels.

## Security

- [Threat model](security/threat-model.md) — trust boundaries, findings register, framework mapping, maturity self-assessment.
- [System card](security/system-card.md) — model, data, evaluations, residual risks.
- Per-week attack evidence: [`evidence/`](evidence/).
