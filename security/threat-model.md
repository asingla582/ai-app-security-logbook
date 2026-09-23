# Threat Model v1

**Version:** 1.0, plus deltas in the Update log: v1.1 (Week 5 RAG), v1.2 (Week 6 indirect injection + output handling) · **Date:** 2026-09-06 · **Milestone:** v0.4 "Trust Foundation"
**Refresh cadence:** revised at each milestone (next at v0.8, then v1.0). A threat
model is evidence, and evidence goes stale; treat anything here as true only as of
the date above and the commit it ships with.

This document is the security spine of the project. It states what the system is,
where the trust boundaries are, what can go wrong, which controls address each risk,
and — honestly — what is still open and when it is scheduled to close.

---

## 1. System overview

A multi-tenant enterprise AI assistant, built as a vertical slice per week.

- **Web** (`apps/web`, Next.js): browser client. Holds a Supabase session (JWT).
- **API** (`apps/api`, FastAPI): all server logic. Verifies the JWT, enforces
  authorization, constructs prompts, calls the model, writes the audit log.
- **Database** (Supabase Postgres): tenants (organizations, memberships),
  conversations/messages, and the `model_calls` audit log. Row Level Security (RLS)
  is enforced at the database, not only in the app.
- **Model gateway** (`apps/api/app/gateway.py`): the single path to the Anthropic
  API. A deterministic fake backs tests and CI so no key is needed there.

### Data flow (a chat turn)

1. Browser sends a message with the user's JWT to the API.
2. API verifies the JWT (`auth.py`), stamps a correlation ID on the request
   (`main.py`), and checks the caller owns the conversation
   (`authz.require_conversation_owner`) — an explicit `user_id` check, independent
   of RLS.
3. API assembles the prompt (`prompting.assemble_chat_prompt`): the system slot is
   filled only from a versioned template file; conversation content can enter only
   as `user`/`assistant` messages.
4. Gateway calls the model. The reply is stored, and a **redacted** copy of input
   and output is written to `model_calls` via a `SECURITY DEFINER` function that
   stamps `user_id` from `auth.uid()` (rows cannot be forged), along with the
   prompt template ref (`chat.vN@<hash>`) and the correlation ID.

---

## 2. Trust boundaries (the closed loop: inputs, actions, outputs)

Defense is organized around three surfaces the industry frames as a closed loop —
what the model can **see** (input), what it can **do** (actions), what it **emits**
(output) — plus the tenant boundary that sits under all of them.

| Boundary | What crosses it | Primary control |
|---|---|---|
| Browser → API | User identity, user message | JWT verification; message size cap (`MAX_INPUT_CHARS`) |
| Tenant → Tenant | Any cross-user/cross-org read or write | Postgres RLS **plus** app-layer ownership checks (`authz.py`) |
| User input → Model (INPUT) | The message text | Structural separation: user text can never occupy the system slot (`prompting.py`) |
| Model → Actions (ACTIONS) | Tool/function calls | **None needed yet — no tools, no retrieval.** Introduced Week 7 |
| Model → User/Log (OUTPUT) | The model's answer | Redaction at the audit boundary (`redaction.py`); output rendering is passthrough text today (hardening in Week 6) |

The single most important property: **authorization never happens inside a prompt.**
Every access decision is made by the application and the database. The model is never
trusted to protect data, enforce ownership, or keep a secret.

---

## 3. Assets and data classification

| Asset | Sensitivity | Where it lives | Notes |
|---|---|---|---|
| User conversations (raw) | High (may contain PII/secrets users paste) | `messages` table, user-scoped by RLS | Deleted on conversation delete (cascade) |
| Audit log | Medium | `model_calls` table | **Redacted** input/output only; survives conversation deletion with `conversation_id` set null |
| Auth tokens / keys | Critical | Supabase; `ANTHROPIC_API_KEY` in `.env` (gitignored) | Service role key is server-only, never sent to the browser |
| System prompt | Low (public in repo) | `apps/api/app/prompts/chat/*.md` | Holds no secrets by design; see RR-W3-3 |

**Data-minimization decision (recorded here as an architectural stance):** filters
leak by design (Week 2 proved it — see RR-W2-1/2). The durable answer to "sensitive
data in the log" is not a better scrubber but storing less: keep metadata and
redacted excerpts, not raw sensitive content. The audit log already stores only
redacted copies. Future logging should default to metadata-first.

---

## 4. Threats and controls, framework-mapped

Mapped to **OWASP LLM Top 10 (2025)**, **NIST AI RMF** (Govern/Map/Measure/Manage),
and **MITRE ATLAS** (adversarial ML tactics). ATLAS references are the closest tactic,
not an exact technique ID.

### Tenant isolation (foundational)
- **Threat:** one user/org reads or writes another's data (IDOR, cross-tenant).
- **Controls:** RLS policies on every table; app-layer ownership checks that compare
  `user_id`/membership explicitly (`authz.py`), so the wall holds even if RLS were
  bypassed; malformed IDs return 404, not a 500. Cross-tenant attempts are logged as
  explicit denies with the correlation ID.
- **OWASP:** LLM06 (excessive agency, indirectly) / general access control ·
  **NIST:** Manage · **ATLAS:** Exfiltration.
- **Evidence:** `tests/rls/`, `apps/api/tests/test_attacks.py`,
  `evidence/week1/`.

### INPUT — prompt injection (direct)
- **Threat:** user input overrides system instructions, hijacks role, or extracts
  the prompt.
- **Controls (structural, primary):** user input cannot occupy the system slot
  (`prompting.py`), asserted with no model in the loop (`test_prompting.py`).
  **Controls (behavioral, secondary):** hardened prompt `chat v3` with an
  authority-proof confidentiality clause.
- **Measurement:** 20-case Promptfoo direct-injection corpus, gated in CI. All
  override/hijack/leak attacks blocked across 10 runs; measured as a rate, not a proof.
- **OWASP:** LLM01 (prompt injection), LLM07 (system prompt leakage) ·
  **NIST:** Measure · **ATLAS:** LLM Prompt Injection.
- **Evidence:** `evals/`, `evidence/week3/`.

### OUTPUT — sensitive data disclosure via logs / responses
- **Threat:** PII/secrets pasted by users land in the audit log or leave through the
  answer.
- **Controls:** application-boundary redaction of both input and output before they
  reach `model_calls` (`redaction.py`); the audit table has no `authenticated` grant
  or RLS policy (server-write-only).
- **OWASP:** LLM02 (sensitive info disclosure), LLM05 (improper output handling) ·
  **NIST:** Manage · **ATLAS:** Exfiltration.
- **Residuals:** RR-W2-1, RR-W2-2 (see §5). Output-channel exfiltration (rendered
  links/images) is **not yet defended** — Week 6.
- **Evidence:** `apps/api/tests/test_redaction.py`, `evidence/week2/`.

### ACTIONS — tool abuse / excessive agency
- **Threat:** injected instruction drives a tool call; confused-deputy across tenants.
- **Status:** **not applicable yet.** The assistant has no tools and no retrieval;
  output is only shown as text. This is why RR-W3-1 (payload splitting) has nil impact
  today. Controls (per-call authz, input validation, rate limits) land Weeks 7-8.
- **OWASP:** LLM06 (excessive agency), LLM10 (unbounded consumption) · **NIST:** Manage.

### Supply chain / model provenance
- **Threat:** compromised dependency or a shift in the hosted model.
- **Controls:** Gitleaks + Trivy in CI; pinned tool versions (e.g. `promptfoo@0.120.27`);
  the eval gate re-runs the attack corpus each release, which doubles as **drift
  detection** for hosted-model updates.
- **OWASP:** LLM03 (supply chain) · **NIST:** Manage · **ATLAS:** ML Supply Chain Compromise.

---

## 5. Findings register (the "close findings" deliverable)

Every documented finding from weeks 1-3, with an honest status. "Closed" here means
*triaged and owned*, not necessarily fixed; deferrals name the week that owns them.

| ID | Finding | Status | Owner / target |
|---|---|---|---|
| W1-RLS | Membership policy let a user grant themselves into any org | **Fixed** (wk1) | Regression test locks it |
| RR-W2-1 | Obfuscated PII (e.g. `x [at] y [dot] com`) slips redaction | **Open, accepted** | Architectural limit of signature matching; mitigated by data-minimization stance (§3) |
| RR-W2-2 | Free-form secrets (e.g. a pasted password) slip redaction | **Open, accepted** | Same; no signature to match. Documented, not silently ignored |
| DoW | Denial-of-wallet: unrestricted signup, no rate limiting | **Deferred → Week 7** | Rate limiting / resource caps |
| RR-W3-1 | Payload splitting steers exact output (`PW`+`NED`) | **Closed** (wk6) | The predicted impact arrived with rendered output and was defanged: split-URL assembly broke 10/10 pre-defense, 0/10 post (see v1.2 delta). Canary stays in the eval suite |
| RR-W3-2 | Over-strict leak gate scored refusals as leaks | **Fixed** (wk3) | Rewritten to coverage measure; re-scored |
| RR-W3-3 | Instruction-leak resistance is behavioral, not structural | **Accepted limit** | Prompt holds no secrets by design; a secret prompt would need a different architecture |
| RR-006 | Document-hosted URLs are allowlist-legal: a forged page's phishing link renders clickable, attributed but live (10/10) | **Open, accepted for now** | Content-trust problem, not an output-channel one; candidate controls (URL reputation, sensitivity-scoped link policy) weighed at v0.8. See v1.2 delta |

No finding is un-triaged. The two "open, accepted" redaction residuals are genuine
and stated plainly rather than closed with a false fix.

---

## 6. Maturity self-assessment (honest)

Scored 1-5 against the five-stage lens (discover/classify, govern, protect, detect,
assure). This is a solo project at week 4; the point is an honest gap map, not a
gold star.

| Stage | Score | Why |
|---|---|---|
| Discover & classify | 2 | Assets known; no automated data classification yet (relevant once RAG lands, Week 5) |
| Govern | 3 | Clear ownership model, least-privilege RLS, versioned prompts; no formal policy doc beyond this |
| Protect | 4 | Strongest area: RLS + app authz, redaction, structural prompt separation, CI security gates |
| Detect | 1 | Weakest area: audit log exists, but no real-time monitoring/alerting. Owned by Week 10 |
| Assure | 3 | Eval gate, this threat model, dated evidence per release; no external audit |

The shape is deliberate: this phase invests in **protect**, and **detect** is
knowingly thin until observability (Week 10).

---

## 7. Out of scope (stated, not ignored)

- **Training-time data/model poisoning and model inversion (OWASP LLM04):** not
  applicable — the project uses a hosted model and never trains or fine-tunes.
- **Provider abstraction / multi-model:** cut from the plan of record.
- **Infrastructure/network hardening (VPC, KMS, TLS termination):** local-dev posture;
  not a production deployment.

---

## 8. Residual risk statement

Prompt injection cannot be fully eliminated; that is field consensus, not a hedge.
This system's honest posture: **user input cannot structurally become a system
instruction** (proven without the model), tenant isolation holds at two independent
layers, and every model-facing safety claim is stated as a measured rate with the
evidence linked. The known-open items (redaction residuals, denial-of-wallet,
output-channel handling) are named above with owners and target weeks. Nothing here
is claimed as "solved."

---

## Update log

A threat model is evidence and evidence goes stale; deltas between milestone
rewrites are recorded here so the document tracks reality, not just release dates.

### v1.1 — Week 5 (2026-09-15): secure RAG

New capability: the assistant can retrieve an organization's uploaded documents and
answer from them. New surface and controls:

- **Retrieval authorization (new, controlled).** Documents and chunks are org-scoped;
  retrieval is a pgvector similarity search that RLS (`is_org_member`) plus an explicit
  org filter constrain to the caller's org *before* ranking. The model never chooses
  what it may read. Proven with no model in the loop, including a crafted-vector case
  (`tests/rls/test_rag_isolation.py`) and the chat path (`apps/api/tests/test_rag_chat.py`).
  Maps to OWASP **LLM08 Vector/Embedding Weaknesses** and general access control.
- **Data lineage (new).** Each `model_calls` row now records the retrieved document ids
  (`sources`), so an answer traces to its prompt template *and* its sources.
- **Embedding data-egress (new, accepted).** Document text is sent to OpenAI at ingest
  to produce embeddings (chosen provider). First tenant-data egress to a third party
  and a second AI vendor: a real data-in-transit / supply-chain consideration
  (OWASP **LLM03**). Tests/CI use a deterministic offline fake and send nothing.
- **Demonstrated open risk → Week 6:** indirect content poisoning via retrieved
  documents. The "reference, not instructions" label stops blunt injection, but a
  red-team against the live model (`evidence/week5/redteam_indirect.py`) showed the
  assistant faithfully relaying a forged document's phishing URL and fake infra
  details to the user, each with a citation. This is not a jailbreak the model can
  refuse; it is RAG trusting retrieved content by design (OWASP **LLM04** data/model
  poisoning, **LLM05** improper output handling). Provenance/trust labels and
  output-side defenses land in Week 6. This is an open finding as of v1.1.

### v1.2 — Week 6 (2026-09-21): indirect injection defenses + output handling

Closes the v1.1 open finding (content poisoning via retrieved documents) as far
as architecture can, and adds the output-side control the roadmap scheduled here
because the year's most-cited LLM CVE (EchoLeak, CVE-2025-32711) exfiltrated
through the *output*, not the input.

- **Context provenance (new, controlled).** Every element entering prompt
  construction carries a server-assigned trust tier — `SYSTEM` (the versioned
  template registry), `USER` (the authenticated conversation), `RETRIEVED`
  (documents) — assigned in `provenance.py`/`routes_chat.py` from the database,
  never from content. Retrieved chunks enter the prompt only inside per-request
  nonce fences whose delimiter syntax is unrepresentable within fenced content
  (`neutralize()`), so a document can neither know nor forge the boundary.
  Sensitivity labels (0005) now propagate into the prompt and the audit record.
  Structural suite: `test_provenance.py`, no model in the loop. Maps to OWASP
  **LLM01**; NIST Manage; ATLAS LLM Prompt Injection.
- **Output handling (new, controlled).** Model output is sanitized server-side
  before it is stored or returned (`output_handling.py`): images never survive;
  a link stays clickable only if its exact URL appears verbatim in the chunks
  retrieved for that request, so a surviving link cannot carry information the
  org's documents did not already contain; everything else is de-fanged to
  visible inert text; non-http(s) schemes never survive. The web client renders
  assistant markdown through a hardened component that independently refuses
  images and raw HTML. Maps to OWASP **LLM05** (improper output handling).
- **Audit lineage completed (new).** `model_calls` now records the trust tier of
  every context element (`context_provenance`) and every sanitizer decision
  (`output_handling`) — allowed, de-fanged, and blocked URLs. A stripped
  exfiltration link is a *detection signal*, and it is now on the record; the
  "answerable question" test (can the audit row alone reconstruct which chunk
  carried the payload and what left the system?) is asserted in
  `test_output_handling.py`.
- **Measured (10 runs per attack, artifact-anchored detector).** Full corpus and
  transcripts in `evidence/week6/`. Constructed-URL exfiltration: canary in an
  outbound URL 8/10 pre-defense → **0/10 post**. Payload splitting across
  documents: 10/10 → 0/10 (closes RR-W3-1's predicted impact). Image-beacon
  relay: 8/10 → 1/10, and the survivor is a text link to a document-hosted URL,
  not an image. Promptfoo: indirect corpus 12/12, direct regression 20/20.
- **New residual RR-006 (open, stated).** The provenance allowlist grants what a
  document *literally contains*, so a forged document's own phishing link still
  renders clickable (10/10), attributed to its source by the chat-v4 framing but
  live. This is a content-trust problem — whether the document is honest — and
  is out of reach of output policy short of de-fanging every link, which would
  also break every legitimate citation. Candidate controls (URL reputation,
  sensitivity-scoped link policy, admin-curated domains) will be weighed at the
  v0.8 milestone. The delimiter-and-framing defense on the input side remains
  probabilistic, not absolute, per field consensus; the structural guarantees
  (fences, allowlist, image ban) are the parts proven without the model.
