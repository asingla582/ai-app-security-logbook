# ADR 0001: Eval harness

**Status:** Accepted · **Date:** 2026-10-18 · **Week:** 9 · **Related:** threat model
v1.5, `evidence/week9/`, `evals/README.md`

## Context

By Week 8 the project measured its defenses in three different ways, and nothing
combined them:

- **Model behavior.** Three Promptfoo suites (direct injection, indirect injection
  through the real RAG assembly, tool-proposal injection through the real
  `propose()`), run by hand through `make eval*` and committed as evidence. Promptfoo
  has been pinned at `promptfoo@0.120.27` since Week 3.
- **Deterministic controls.** pytest (API and RLS suites against the local Supabase
  stack) and vitest for the web client: tenant isolation, the approval gate, output
  handling, rate limits, structural prompt separation.
- **Trajectories.** What the app decided at each step of a tool or approval flow.
  This is not in the model transcript. It is recorded in the database: `tool_calls`
  holds the `proposed → decided → executed` sequence and `pending_actions` holds each
  parked write and its status. A reply could say "I saved your note" while
  `tool_calls` says `pending_approval` and no note exists; the database is what
  counts.

Two facts made the gap concrete. The CI eval job had skipped in all 5 runs that had
one, and reported success each time (RR-W9-1). And v0.8 was released on a build that
was red in two other jobs (RR-W9-2). The project needed one place where every check
is scored, a threshold fixed in advance, and a rule that a check which did not run
counts as a failure.

The constraints come from the project itself: evidence must be committed and
reproducible by a stranger from a fresh clone, local Supabase only, no new vendor
that sees tenant-shaped data without a reason, and the model id and template ref read
from the app (`app.config.CHAT_MODEL`, `app.prompting.CHAT_TEMPLATE.ref`), never
hardcoded.

## Options

### 1. Promptfoo (in use since Week 3)

An open-source CLI that runs a corpus of test cases against a prompt and a provider
and scores each output with assertions (string checks, JavaScript, model-graded
rubrics). It supports custom Python providers, which is how the suites call the
app's own gateway and `propose()` rather than a copy of the prompt. It writes a JSON
report and supports `--repeat`.

- For: already written and debugged over six weeks of corpora; runs locally and in
  CI with `--no-cache --no-share`, so nothing leaves the machine except the model
  calls themselves; the report is a file the scorecard can parse.
- Against: a moving dependency (the pin has to be raised by hand); its report format
  has quirks the scorecard must handle (it sets `error` on assertion failures as well
  as provider errors, which the scorecard first misread, fixed in `d59db00`); it sees
  only what the provider returns, so it cannot see database state.

### 2. agentevals

An open-source package from the LangChain project for evaluating agent trajectories.
As documented, it takes a trajectory as a sequence of messages with tool calls and
compares it to a reference (strict, unordered, subset or superset matching) or asks
an LLM judge to grade it, with helpers for LangGraph threads.

- For: built for exactly the question "did the agent take the right steps", with
  match modes this project would otherwise write by hand; works offline for the
  match-based evaluators.
- Against, for this app: the trajectory that matters here is not a message list. A
  tool call can be proposed in the transcript and then rejected, parked, expired or
  executed by the app, and only `tool_calls` and `pending_actions` record which. To
  use agentevals the tests would first have to export database rows into its message
  format and then score the export, which adds a translation step that can itself be
  wrong. The app has no agent framework, so the LangGraph helpers do not apply. It
  would be a new dependency for checks that are a few lines of SQL and `assert`.

### 3. LangSmith or another hosted harness

Hosted platforms (LangSmith, and similar services) that store datasets, run
experiments, trace calls and show results over time in a dashboard, with comparison
between runs and human annotation.

- For: history and comparison across runs without building any of it; a UI for
  reading transcripts, which is the step that caught this project's detector
  bugs; tracing that Week 10 (observability) may want anyway.
- Against, for this project: results live in a vendor account rather than in the
  repo, so a reader of the logbook cannot check them from a clone; it needs an
  account and an API key, which is the same kind of secret dependency that kept the
  old CI gate from running; the eval inputs and outputs (document text, replies) go
  to a third vendor, after Anthropic and OpenAI. None of this is a defect in those
  products; it is a mismatch with a project whose evidence has to be public and
  reproducible.

## Decision

1. **Model suites stay in Promptfoo**, pinned at `promptfoo@0.120.27`, run with
   `--no-cache --no-share` through the app's own providers. Recorded runs use
   repeat 10; CI uses repeat 3.
2. **Trajectory checks live in pytest against the real local database.**
   `apps/api/tests/test_trajectories.py` (7 tests) drives the API with the
   deterministic fake gateway, then reads `tool_calls` and `pending_actions` directly
   and asserts the ordered sequence: a read tool, an approved write, a denied write,
   an expired write, an injected `org_id`, a wrong hash followed by the right one, and
   a sweep that no `create_note` call is `executed` without an `executed` pending
   action. The trajectory is recorded in the database, not in the transcript, so that
   is where it is checked.
3. **The scorecard is a committed file, not a hosted dashboard.**
   `scripts/scorecard.py` runs every suite, maps each test file to one of twelve rows
   (`scripts/scorecard_config.py`), applies thresholds fixed before the first run, and
   writes `scorecard.md` and `scorecard.json`. Recorded runs are committed under
   `evidence/<week>/`. A required row that did not run is FAIL. The CI `scorecard`
   job runs the same command at repeat 3 and uploads the result as an artifact.

## Consequences

- **Every number is checkable from a clone.** The recorded scorecard names the
  commit, model id, template ref, Promptfoo version and repeat count, and `make
  scorecard` reproduces it, given a key and about 380 paid model calls.
- **A failing run stays on the record.** Run 1 failed (Indirect injection 22/23) and is
  kept as `evidence/week9/scorecard-run1-fail.md`. The detector fix and run 2 are
  separate, dated commits. A dashboard that keeps only the latest run would make this
  easier to lose.
- **No history view.** Comparing runs means diffing committed files. That is
  workable at one recorded run per week and would not be at many runs a day.
- **Reading transcripts stays manual.** The scorecard scores what each detector
  reports. RR-W9-3 was a detector scoring a mention as an exfiltration, found only by
  reading the replies. Nothing in this decision automates that step.
- **The trajectory tests do not test the model.** They use the fake gateway, so they
  show that the app records and enforces each step correctly for a given proposal,
  not what a real model would propose. Whether a document can steer the real model
  into proposing an action is the Promptfoo tool-injection suite's job.
- **Maintenance falls on this repo.** A new test file must be mapped in
  `scorecard_config.py` or it lands in "Other deterministic"; the Promptfoo pin and
  its report quirks have to be tracked by hand; the scorecard's own tests
  (`tests/scorecard/`) are part of the gate.
- **CI depends on one secret.** The model rows need the author's `ANTHROPIC_API_KEY`
  repository secret. Without it the job fails on push and same-repo PRs by design,
  and fork PRs get a stated partial pass with the model rows not run.
- **Revisit if** the project adds an agent framework whose native trajectory format
  matches what the app records, or if Week 10 observability brings in tracing that
  would also hold eval history. Either would change the trade-off above.
