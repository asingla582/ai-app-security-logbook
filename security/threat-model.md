# Threat Model v1

**Version:** 1.0, plus deltas in the Update log: v1.1 (Week 5 RAG), v1.2 (Week 6 indirect injection + output handling), v1.3 (Week 7 secure tool calling), v1.4 (Week 8 human approval) · **Date:** 2026-10-07 · **Milestone:** v0.4 "Trust Foundation" → v0.8
**Refresh cadence:** revised at each milestone (next at v1.0). A threat
model is evidence, and evidence goes stale; treat anything here as true only as of
the date above and the commit it ships with.

This document is the security spine of the project. It states what the system is,
where the trust boundaries are, what can go wrong, which controls address each risk,
and (honestly) what is still open and when it is scheduled to close.

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
   (`authz.require_conversation_owner`): an explicit `user_id` check, independent
   of RLS.
3. API assembles the prompt (`prompting.assemble_chat_prompt`): the system slot is
   filled only from a versioned template file; conversation content can enter only
   as `user`/`assistant` messages.
4. Gateway calls the model. The reply is stored, and a **redacted** copy of input
   and output is written to `model_calls` via a `SECURITY DEFINER` function that
   stamps `user_id` from `auth.uid()` (rows cannot be forged), along with the
   prompt template ref (`chat.vN@<hash>`) and the correlation ID.

### Data flow (an approval-required action, Week 8)

When the model proposes a tool marked `requires_approval` (today only `create_note`),
the turn above does not execute it. The approve leg is a separate request:

1. **Proposal.** The app validates and authorizes the proposed args as in Week 7,
   then parks them instead of executing (`tool_exec._park_for_approval`). Provenance
   flags (URLs with their source document, text copied from a document) are computed
   from the args and the chunks the model saw that turn.
2. **Pending.** `create_pending_action` (SECURITY DEFINER, migration 0011) stores the
   exact args, computes their SHA-256 in the database, and sets a 15-minute expiry.
   The trajectory row in `tool_calls` reads `pending_approval`. The model's reply is
   told the action is waiting and has not happened.
3. **Approve request.** The requester's browser shows the approval card and posts
   `POST /actions/{id}/approve` with only the hash the card displayed. Any other
   field is rejected (422).
4. **Claim.** `claim_pending_action` locks the row, checks requester, current
   membership, status, expiry and hash, and flips it to `approved` once. The claim
   commits before anything runs.
5. **Execute as caller.** The app re-checks the registry, re-validates the args read
   from the claimed row (never from the request), executes on the caller's
   RLS-scoped connection, and records `executed` or `failed` via
   `complete_pending_action`.

---

## 2. Trust boundaries (the closed loop: inputs, actions, outputs)

Defense is organized around three surfaces the industry frames as a closed loop:
what the model can **see** (input), what it can **do** (actions), what it **emits**
(output), plus the tenant boundary that sits under all of them.

| Boundary | What crosses it | Primary control |
|---|---|---|
| Browser → API | User identity, user message | JWT verification; message size cap (`MAX_INPUT_CHARS`) |
| Tenant → Tenant | Any cross-user/cross-org read or write | Postgres RLS **plus** app-layer ownership checks (`authz.py`) |
| User input → Model (INPUT) | The message text | Structural separation: user text can never occupy the system slot (`prompting.py`) |
| Model → Actions (ACTIONS) | Tool/function calls | App-side validation and authorization; execution as the caller (Week 7). Writes need the requester's approval of a stored, hash-locked row (Week 8) |
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
| Pending actions | Confidential (tenant) | `pending_actions` table (migration 0011) | Holds the **unredacted** args of each parked action, because they are what will execute. Owner-select only (RLS `requester = auth.uid()`); no direct insert, update or delete for `authenticated`; immutability trigger on every role. Audit copies in `tool_calls` stay redacted |
| System prompt | Low (public in repo) | `apps/api/app/prompts/chat/*.md` | Holds no secrets by design; see RR-W3-3 |

**Data-minimization decision (recorded here as an architectural stance):** filters
leak by design (Week 2 proved it; see RR-W2-1/2). The durable answer to "sensitive
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

### INPUT: prompt injection (direct)
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

### OUTPUT: sensitive data disclosure via logs / responses
- **Threat:** PII/secrets pasted by users land in the audit log or leave through the
  answer.
- **Controls:** application-boundary redaction of both input and output before they
  reach `model_calls` (`redaction.py`); the audit table has no `authenticated` grant
  or RLS policy (server-write-only).
- **OWASP:** LLM02 (sensitive info disclosure), LLM05 (improper output handling) ·
  **NIST:** Manage · **ATLAS:** Exfiltration.
- **Residuals:** RR-W2-1, RR-W2-2 (see §5). Output-channel exfiltration (rendered
  links/images) is **not yet defended** (Week 6).
- **Evidence:** `apps/api/tests/test_redaction.py`, `evidence/week2/`.

### ACTIONS: tool abuse / excessive agency
- **Threat:** an injected instruction drives a tool call; a confused-deputy call acts
  across tenants; a flood of expensive calls runs the system into the ground.
- **Status:** **controlled as of Week 7.** The assistant has two tools,
  `search_documents` (read) and `create_note` (write), behind a single-step pipeline:
  the model only *proposes* a tool (native tool-use), and the application authorizes,
  validates, executes, and records it.
- **Controls (structural, primary):** the model never chooses the tenant: tool
  argument models (`tools.py`) carry no `org_id`/`user_id` and forbid extra fields, so
  a model-supplied tenant is rejected at validation; a registry-load assertion fails
  the app if a future tool omits `extra="forbid"`. Tools execute on the caller's
  RLS-scoped connection (`tool_exec.py`), so a bypassed app check still meets RLS.
  Single-step is structural: the second (answering) model call is offered no tools, so
  the turn cannot chain. Tool results re-enter the model only through the one assembly
  path as `RETRIEVED`-tier chunks (the Week 6 invariant). The output allowlist is built
  only from document-derived chunks (chat retrieval and `search_documents` results).
  App-generated chunks (`pending-approval`, `note-confirmation`) echo model-written
  arguments, so they are excluded; a URL the model puts in a proposed title stays
  de-fanged (closed in the week 8 final review).
- **Controls (resource, primary):** a per-org daily model-call ceiling (the
  denial-of-wallet cap) and a per-user tool-rate cap, counted from the audit tables via
  `SECURITY DEFINER` functions (`limits.py`, migration 0009) and returning HTTP 429 on
  breach. Every proposal is recorded as a `proposed → decided → executed` trajectory in
  `tool_calls` (migration 0008), server-write-only like `model_calls`.
- **Measurement:** `make eval-tools` (injection-driven invocation, 6/6 at the model
  layer); `evidence/week7/` live red team (injection-driven writes held; DoW ceiling
  engages; cross-tenant and tampering blocked deterministically in
  `test_tool_attacks.py`).
- **OWASP:** LLM06 (excessive agency), LLM10 (unbounded consumption) · **NIST:** Manage
  · **ATLAS:** LLM Prompt Injection, Cost Harvesting.
- **Residuals:** RR-W7-1 (authorized-but-steered write, closed in Week 8 by the
  approval gate below), RR-W7-2 (fixed-window race, accepted). See §5.

### ACTIONS: human approval gate (HITL, Week 8)
- **Threat:** injected document content steers *what* a user-authorized write records
  (RR-W7-1); an attacker or a buggy client changes the args between approval and
  execution; an approval is forged, replayed, raced or used after it expires; a
  document tells the model the gate does not apply; the human approves from a card
  that hides what will be written ("approval theater").
- **Status:** **controlled as of Week 8**, with the human-judgment residual stated
  (RR-W8-1).
- **Controls (structural, primary):**
  - *Registry flag.* `create_note` is `requires_approval=True` in the tool registry
    (`tools.py`); an approval-required tool is parked, never executed, in the turn
    that proposed it (`tool_exec.py`).
  - *DB-owned hash.* `create_pending_action` computes `args_sha256` in the database
    from the stored args; the client never supplies it. The approve request carries
    only that hash, and the request model forbids extra fields, so args cannot ride
    along (422).
  - *Immutability trigger.* `pending_actions_guard` rejects any change to the locked
    columns (args, hash, flags, requester, org, expiry) and any illegal status
    transition, for every role including the table owner and the definer functions.
    Rows cannot be deleted.
  - *Owner-select only.* `authenticated` may only SELECT its own rows; every write
    is a SECURITY DEFINER function. `anon` EXECUTE is revoked on these functions and
    on 0008's `record_tool_proposal`/`finalize_tool_call` (Supabase's default ACL
    grants `anon` directly, so revoking from `public` alone was not enough; caught
    by task review).
  - *Single-use claim.* `claim_pending_action` takes the row `FOR UPDATE` and moves
    `pending → approved` once; a second or concurrent claim gets `already_decided`
    (409), an overdue one is marked `expired` (410), a wrong hash gets 409.
  - *Membership re-check.* The claim requires the caller to be the requester and a
    current member of the row's org; anyone else gets `not_found` (404).
  - *Args from the row only.* After the claim, the app re-checks the registry and
    re-validates the args it read from the claimed row, then executes on the
    caller's RLS-scoped connection (`approvals.py`). Nothing from the approve
    request except the id and hash reaches the executor.
- **Controls (human-facing):** provenance flags computed server-side at proposal
  time and frozen on the row (`approval_flags.py`): every URL in the action with the
  document it came from (or "written by the assistant"), including scheme-less forms
  of hosts a retrieved document mentioned, and 8-word spans copied from a document.
  Card v2 (`ApprovalCard.tsx`) shows the full body as plain text, lists links as
  host plus source and never as anchors, shows copied spans and the expiry time.
- **Measurement:** `evidence/week8/redteam-run.txt`, 12/12 held, 0 broke (mutation,
  forgery, replay, injection bypass over 10 live runs, trajectory). Approval theater
  (`theater-run.txt`, card v1, 10 runs): link stored and hidden on the card in 5/10
  by exact match, 7/10 in any form. After card v2 and flag hardening
  (`theater-run-v2.txt`, 10 fresh runs): the link landed in 6/10 in any form and
  was flagged with its source in 6/6 of those.
- **OWASP:** LLM06 (excessive agency), LLM01 (indirect prompt injection) · **NIST:**
  Manage · **ATLAS:** LLM Prompt Injection, User Execution.
- **Residuals:** RR-W8-1 (an informed human can approve a bad action), RR-W8-2 (flag
  detection limits), RR-W8-3 (requester can forge their own completion record). See §5.
- **Evidence:** `supabase/migrations/0011_pending_actions.sql`,
  `apps/api/app/approvals.py`, `evidence/week8/` (including a written walkthrough).

### Supply chain / model provenance
- **Threat:** compromised dependency or a shift in the hosted model.
- **Controls:** Gitleaks + Trivy in CI; pinned tool versions (e.g. `promptfoo@0.120.27`);
  the eval gate re-runs the attack corpus each release, which doubles as **drift
  detection** for hosted-model updates.
- **OWASP:** LLM03 (supply chain) · **NIST:** Manage · **ATLAS:** ML Supply Chain Compromise.

---

## 5. Findings register (the "close findings" deliverable)

Every documented finding to date, with an honest status. "Closed" here means
*triaged and owned*, not necessarily fixed; deferrals name the week that owns them.

| ID | Finding | Status | Owner / target |
|---|---|---|---|
| W1-RLS | Membership policy let a user grant themselves into any org | **Fixed** (wk1) | Regression test locks it |
| RR-W2-1 | Obfuscated PII (e.g. `x [at] y [dot] com`) slips redaction | **Open, accepted** | Architectural limit of signature matching; mitigated by data-minimization stance (§3) |
| RR-W2-2 | Free-form secrets (e.g. a pasted password) slip redaction | **Open, accepted** | Same; no signature to match. Documented, not silently ignored |
| DoW | Denial-of-wallet: unrestricted signup, no rate limiting | **Addressed** (wk7) | Per-org daily model-call ceiling + per-user tool-rate cap, 429 on breach (`limits.py`). The v1.3 red team caught the first implementation counting zero under RLS; fixed via SECURITY DEFINER counters (migration 0009) |
| RR-W7-1 | Injected content can steer *what* a user-authorized `create_note` writes; the write executes with no human approval | **Closed** (wk8) | Writes now need the requester's approval, and what executes is the stored, hash-locked row. The theater run showed why the card matters: on card v1 a document's phishing link reached the stored note and was hidden from the card in 7/10 runs (any form; 5/10 exact URL). After card v2 and flag hardening, every run where the link landed was flagged with its source (6/6). The human's judgment is RR-W8-1 |
| RR-W7-2 | Fixed-window rate limits race under concurrency | **Open, accepted** | Simpler than a distributed token bucket and honest at this scale; a burst at a window edge can slightly exceed the cap |
| RR-W7-3 | Rate-limit counts read audit tables the `authenticated` role cannot see (RLS), so `count(*)` returned 0 and limits were silent no-ops | **Fixed** (wk7) | Caught by the live red team, not the mocked unit tests. SECURITY DEFINER counting functions (migration 0009); regression test exercises the real count path |
| RR-W3-1 | Payload splitting steers exact output (`PW`+`NED`) | **Closed** (wk6) | The predicted impact arrived with rendered output and was defanged: split-URL assembly broke 10/10 pre-defense, 0/10 post (see v1.2 delta). Canary stays in the eval suite |
| RR-W3-2 | Over-strict leak gate scored refusals as leaks | **Fixed** (wk3) | Rewritten to coverage measure; re-scored |
| RR-W3-3 | Instruction-leak resistance is behavioral, not structural | **Accepted limit** | Prompt holds no secrets by design; a secret prompt would need a different architecture |
| RR-W8-1 | An informed human can still approve a bad action. HITL buys informed consent, not correctness | **Open, accepted** | Mitigated by provenance flags (links with source, copied spans, full body on the card). Candidate further controls: a four-eyes rule for flagged actions, URL reputation |
| RR-W8-2 | Flag detection limits: scheme-less links are detected only for hosts a retrieved document mentioned, by whitespace token; obfuscated forms (`host[.]example`, text glued to the host, port or userinfo forms) are not flagged; a link the model invents with no document source is flagged "written by the assistant" only if it has a scheme | **Open, accepted** | The card always shows the full body, so the human can still read an unflagged link. Flags are an aid, not a filter |
| RR-W8-3 | `complete_pending_action` is callable by the requester directly after their own claim, so a user can write a forged "executed" audit trail (and an unredacted `result_summary`) for their **own** action without running it | **Open, accepted** | Same pattern as Week 7's `finalize_tool_call`. No privilege gain and no cross-user effect; audit integrity only |
| RR-006 | Document-hosted URLs are allowlist-legal: a forged page's phishing link renders clickable, attributed but live (10/10) | **Accepted** (v0.8) | Weighed at v0.8 and accepted. This is a content-trust problem (is the document honest?), not an output-channel one. Links are attributed to their source, and the approval card's link listing applies the same idea to writes. Candidate controls if it needs to close: URL reputation, a sensitivity-scoped link policy, non-clickable document links in chat replies. See v1.2 and v1.4 deltas |

No finding is un-triaged. The "open, accepted" residuals are genuine and stated
plainly rather than closed with a false fix.

---

## 6. Maturity self-assessment (honest)

Scored 1-5 against the five-stage lens (discover/classify, govern, protect, detect,
assure). This is a solo project, re-scored at week 8 (v0.8); the point is an honest
gap map, not a gold star.

| Stage | Score | Why |
|---|---|---|
| Discover & classify | 2 | Assets known; no automated data classification yet (relevant once RAG lands, Week 5) |
| Govern | 3 | Clear ownership model, least-privilege RLS, versioned prompts; no formal policy doc beyond this |
| Protect | 4 | Strongest area: RLS + app authz, redaction, structural prompt separation, output sanitizer, tool validation and rate caps, and (Week 8) a hash-locked, single-use approval gate for writes. Not a 5: the last decision on a write is a human's (RR-W8-1), and flag detection has known gaps (RR-W8-2) |
| Detect | 1 | Weakest area, unchanged. The audit records more signals (approve/deny outcomes such as `hash_mismatch` and `expired`, the `pending_approval → approved → executed` trajectory), and the red team's orphan query (no executed write without an executed approval) can be run by hand. Nothing alerts on any of it, and RR-W8-3 means a user's own completion record is not trustworthy. Owned by Week 10 |
| Assure | 3 | Eval gate, this threat model, dated evidence per release; no external audit |

The shape is deliberate: this phase invests in **protect**, and **detect** is
knowingly thin until observability (Week 10).

---

## 7. Out of scope (stated, not ignored)

- **Training-time data/model poisoning and model inversion (OWASP LLM04):** not
  applicable; the project uses a hosted model and never trains or fine-tunes.
- **Provider abstraction / multi-model:** cut from the plan of record.
- **Infrastructure/network hardening (VPC, KMS, TLS termination):** local-dev posture;
  not a production deployment.

---

## 8. Residual risk statement

Prompt injection cannot be fully eliminated; that is field consensus, not a hedge.
This system's honest posture: **user input cannot structurally become a system
instruction** (proven without the model), tenant isolation holds at two independent
layers, and every model-facing safety claim is stated as a measured rate with the
evidence linked. Since Week 8, a write executes only after its requester approves the
exact stored row, which the database will not let anyone alter; whether that approval
is a good decision is still a human judgment (RR-W8-1). The known-open items
(redaction residuals, fixed-window rate limits, flag detection limits, document-hosted
links) are named above with owners or an accepted status. Nothing here is claimed as
"solved."

---

## Update log

A threat model is evidence and evidence goes stale; deltas between milestone
rewrites are recorded here so the document tracks reality, not just release dates.

### v1.1 · Week 5 (2026-09-15): secure RAG

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

### v1.2 · Week 6 (2026-09-21): indirect injection defenses + output handling

Closes the v1.1 open finding (content poisoning via retrieved documents) as far
as architecture can, and adds the output-side control the roadmap scheduled here
because the year's most-cited LLM CVE (EchoLeak, CVE-2025-32711) exfiltrated
through the *output*, not the input.

- **Context provenance (new, controlled).** Every element entering prompt
  construction carries a server-assigned trust tier (`SYSTEM` (the versioned
  template registry), `USER` (the authenticated conversation), `RETRIEVED`
  (documents)), assigned in `provenance.py`/`routes_chat.py` from the database,
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
  (`output_handling`): allowed, de-fanged, and blocked URLs. A stripped
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
  live. This is a content-trust problem (whether the document is honest), and
  is out of reach of output policy short of de-fanging every link, which would
  also break every legitimate citation. Candidate controls (URL reputation,
  sensitivity-scoped link policy, admin-curated domains) will be weighed at the
  v0.8 milestone. The delimiter-and-framing defense on the input side remains
  probabilistic, not absolute, per field consensus; the structural guarantees
  (fences, allowlist, image ban) are the parts proven without the model.

### v1.3 · Week 7 (2026-09-29): secure tool calling

Gives the assistant its first actions and answers the ACTIONS threat that had been
"not applicable yet" since Week 3. The stance is the same as everywhere else in the
project: the model proposes, the application decides.

- **Tool registry + single-step pipeline (new, controlled).** Two tools,
  `search_documents` (read) and `create_note` (write), are declared in `tools.py`. The
  model proposes a tool via the native tools API; the app validates arguments
  (Pydantic, `extra="forbid"`), authorizes, executes on the caller's RLS-scoped
  connection, then answers from a second model call offered **no** tools, so a turn
  cannot chain. The tool result re-enters the model only through the one assembly path
  as a `RETRIEVED`-tier chunk (the v1.2 invariant), fenced and allowlisted like any
  document. System prompt `chat v5` adds the tool paragraph; direct 20/20 and indirect
  12/12 regressions hold against it. Maps to OWASP **LLM06**.
- **Tenant is never model-chosen (structural).** Tool arguments carry no
  `org_id`/`user_id`; org is the conversation's own tenant, identity the session. A
  proposal naming another org is rejected at validation. Cross-tenant confused-deputy
  is therefore impossible by construction, proven without the model in
  `test_tool_attacks.py`.
- **Resource caps (new, controlled).** A per-org daily model-call ceiling (the
  denial-of-wallet cap) and a per-user tool-rate cap (`limits.py`), returning HTTP 429
  on breach. Every proposal is recorded as a `proposed → decided → executed` trajectory
  in `tool_calls` (migration 0008), server-write-only. Maps to OWASP **LLM10**.
- **The break the red team caught (fixed).** The first rate-limit implementation
  counted from `model_calls`/`tool_calls` on the caller's `authenticated` connection,
  but those audit tables are RLS-with-no-policy, so the count was always zero and the
  limits did nothing. The mocked unit tests missed it; the live pipeline run
  (`evidence/week7/redteam_week7.py`) exposed it when a flood never tripped. Fixed with
  `SECURITY DEFINER` counting functions (migration 0009) and a regression test that
  exercises the real count. This is RR-W7-3; it is the reason a live run earns its cost
  over a mock.
- **Measured.** Model-layer injection-driven tool invocation: `make eval-tools` 6/6
  (the model refuses document-ordered writes; a detector assertion that flagged a
  codename *mentioned* in a plain reply was corrected to score the write artifact, the
  recurring "score the deliverable, not the mention" rule). Full pipeline: injected
  writes produced no note (`tool_calls=[]`) across runs; DoW ceiling engages after the
  fix (201×4 then 429). Cross-tenant and parameter tampering blocked deterministically.
- **New residual RR-W7-1 (open → Week 8).** A user-authorized `create_note` executes
  immediately with no human approval, so injected content can still steer *what* an
  otherwise-legitimate write records. The app authorizes it because it is genuinely the
  caller's own org; only a human-in-the-loop gate closes the attacker-chosen-content
  case. `create_note` is the designated HITL tool for Week 8. RR-W7-2 (fixed-window
  race) is accepted at this scale.

### v1.4 · Week 8 (2026-10-07): human approval (HITL)

Closes RR-W7-1. A write proposed by the model no longer executes in the turn that
proposed it; the requester approves the exact stored action in a separate request.
The stance holds: the model proposes, the application decides, and for writes the
human decides too.

- **Approval records (new, controlled).** Migration 0011 adds `pending_actions`:
  owner-select only under RLS, every write through a SECURITY DEFINER function, an
  immutability trigger that applies to every role (including the table owner), and
  no deletes. The args hash is computed by the database, never by the client. A task
  review caught that Supabase's default ACL grants `anon` EXECUTE directly, so the
  migration also revokes it from `anon` on the new functions and on 0008's trajectory
  functions. Maps to OWASP **LLM06**; NIST Manage.
- **What executes is what was approved (structural).** `POST /actions/{id}/approve`
  accepts only the hash the card displayed (extra fields 422). The claim is
  single-use, row-locked, requester-anchored, membership-checked and expiry-checked;
  the app then re-checks the registry, re-validates the args read from the claimed
  row, and executes them as the caller. `create_note` carries
  `requires_approval=True` in the registry. Chat template `chat v6` adds one
  sentence: an action awaiting approval has not happened, and the reply must say so.
- **The break the live run caught: approval theater.** The gate was sound, and the
  human still lost. A plausible onboarding doc carried a lookalike SSO link
  (`https://sso-acme-verify.example/login`) in step 4 of its VPN steps; the user asked
  to save those steps. Card v1 showed the title and the first 120 characters of the
  body. Over 10 live runs (`evidence/week8/theater-run.txt`) `create_note` was
  proposed 10/10 and every approval returned HTTP 200. The full URL was in the
  proposed args in 5/10, all 5 stored after approval and hidden on card v1. Reading
  the transcripts found 2 more runs that carried the scheme-less form
  (`sso-acme-verify.example/login`), stored and hidden too and not flagged by
  URL-only extraction: 7/10 in any form. In the landing runs the model also wrote its
  own "verify with IT" caveat into the note body, always past character 120, so the
  card the user approved from hid both the link and the model's own warning.
- **The fix (new, controlled).** Provenance flags computed server-side at proposal
  time and frozen on the row (`approval_flags.py`): every URL with its source
  document or "written by the assistant", scheme-less forms of hosts a retrieved
  document mentioned, and 8-word copied spans. Hardening after the first run added
  scheme-less hosts, a normalized comparison and any-case schemes; a review then
  made a malformed URL (for example a fullwidth `#` in a document) tolerated instead
  of crashing every proposal. Card v2 shows the full
  body as plain text, lists links as host plus source and never as anchors, and
  shows copied spans and the expiry time.
- **Measured.** After the fix (`theater-run-v2.txt`, 10 fresh runs): `create_note`
  proposed 10/10; the link reached the args in 6/10 in any form (4/10 as the full
  URL) and was flagged with source `onboarding.md` in 6/10, which is every landing
  run (6/6). Two of those were scheme-less and were caught only by the hardening.
  The metric scores the flag data the card renders; rendering itself is covered by
  unit tests. Structural red team (`redteam-run.txt`): 12/12 held, 0 broke, covering
  mutation (direct update as user and as table owner, altered hash, args smuggled
  in the body), forgery (direct insert, another user approving, `complete()` on a
  pending row), replay (twice, two concurrent claims, after expiry), an injected
  "pre-approved by IT" document (10/10 runs with zero notes), and the trajectory
  check (0 executed `create_note` rows without an executed approval). Regression on
  `chat v6`: direct 20/20, indirect 12/12, tools 6/6, the Week 7 levels.
- **Residuals.** RR-W7-1 **closed**. New, open and accepted: RR-W8-1 (an informed
  human can still approve a bad action; HITL buys informed consent, not
  correctness), RR-W8-2 (flag detection limits), RR-W8-3 (a requester can write a
  forged completion record for their own action). RR-006 is **accepted at v0.8**:
  it is a content-trust problem, links are attributed to their source, and the
  approval card's link listing applies the same idea to writes; candidate controls
  are URL reputation, a sensitivity-scoped link policy and non-clickable document
  links in chat replies. Stated limit, not a residual: if the database errors
  between claim and completion, a row can stay `approved`; this fails closed and
  nothing runs twice.
