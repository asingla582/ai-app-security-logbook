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

You need Docker and Node 20+. Everything runs against a local Supabase stack; no
accounts or secrets required.

```
cp .env.example .env      # local defaults already work
make up                   # start Supabase + app, seed two demo orgs
make test                 # run every test suite
make attack               # run the cross-tenant attack suite and capture evidence
```

Then open http://localhost:3000 and sign in as `alice@example.com` /
`Password123!`. You will see only Org A. Bob's org and notes are unreachable, by
the database and by the API.

## Status

**Week 1 shipped — Trust Foundation.** Auth, organizations, and notes, with tenant
isolation enforced by Postgres Row Level Security and a defense-in-depth
authorization layer in the API. A five-part cross-tenant attack suite runs in CI;
every attempt is denied and logged. See [`evidence/week1/`](evidence/week1/).

Next: Week 2 introduces the first AI feature — a chat over a thin model gateway,
with an audit log of every model call and PII redaction at the application boundary.
