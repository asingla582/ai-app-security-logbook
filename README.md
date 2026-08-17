# AI App Security Logbook

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

## Status

Pre-build. Week 1 (foundation: auth, organizations, Row Level Security, CI) starts next.
