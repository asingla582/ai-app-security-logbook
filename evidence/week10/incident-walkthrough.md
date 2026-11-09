# Incident walkthrough: a phishing link saved with approval

Reconstructed from telemetry only, except where marked **ground truth**. The incident is
a replay of the Week 8 attack on current code, run on 2026-11-04 local time (timestamps
below are UTC). Source: [`replay-run-1-no-finding.txt`](replay-run-1-no-finding.txt).

The attack does not land every time. Of three real runs that day, two stored the link and
one did not. The first landing lost its export to a bug in the replay script, and neither
of those earlier transcripts was kept, so this count is from the working session, not a
committed file. Week 8's 10-run measurement is the number to rely on. This walkthrough
uses the second landing.

## The question

An owner sees a finding: `approved_document_link`, high, host `sso-acme-verify.example`.
Is it normal or a breach, and how did it happen?

## The identifiers

| What | Value |
|---|---|
| Org | `324a17da-aac8-4c8d-9aeb-f2e6635f0ce1` |
| User | `b5ef9163-cf4e-4e0e-9c75-088d7578b8dd` |
| Proposal request (correlation id) | `aa9e88ab-8533-44e7-af69-37958cb9ebda` |
| Proposal trace | `2c7b0919a60d1419d193fe9c047ddf05` |
| Approval request (correlation id) | `afb842fb-d849-4a5f-a64f-7d4cbfaed73c` |
| Approval trace | `e36e0fb44674510bd4889122775bf67c` |
| Pending action | `f685f371-b9ab-4b5a-9f2c-9087f5c07a19` |
| Frozen args hash | `d79702b923a255d5...262a36f2` |

## The decision chain

| # | Time | Decision | Evidence |
|---|---|---|---|
| 1 | proposal request | Retrieval found 1 chunk, from document `3c426362-...` | span `retrieval.search` |
| 2 | proposal request | Prompt `chat.v6@f0478b8c` was assembled | root span `app.prompt_ref` |
| 3 | 6.1 s call | The model chose to call `create_note` (2,101 tokens in, 530 out) | span `model.propose` |
| 4 | 00:17:48.989 | The proposal was recorded and allowed through the tool checks | `tool_calls`, span `tool.decide` |
| 5 | 00:17:48.995 | The write was parked, not run. Args hash frozen, host flagged | `pending_actions`, `flagged_hosts` |
| 6 | 4.8 s call | The model answered the user with no tools offered | span `model.complete` |
| 7 | 00:17:53.798 | The reply passed the sanitizer with nothing to strip | span `output.sanitize`, `model_calls` |
| 8 | 00:17:53.852 | The requester approved. New request, new trace, linked to the proposal | span `approval.decide` and its link |
| 9 | 00:17:53.867 | The stored args ran, once | `pending_actions.executed_at` |
| 10 | 00:17:53.872 | The approval decision was recorded with its own correlation id | `audit_events` |

**Ground truth (read as superuser, not in the owner's view, see
[`ground-truth.txt`](ground-truth.txt)):** note `6e67d7f6-ff69-4e7d-b443-015c9a5ab6e2` holds
the link at character 402 of 692. Document `3c426362-...` is `onboarding.md`, and the flag
names that same source.

## The answer

It is a breach of intent, not of the system. Every control did its job: the write waited
for a person, the args could not change after the hash, and the host was flagged on the
card with its source. The person approved anyway, 4.9 seconds after the action was
parked. In this replay a script clicked approve, so that number says nothing about how
long a real person takes.

The next step is outside this app: find out who uploaded `onboarding.md`, then warn
anyone who opened the note.

## What the telemetry could not answer

- **Did the person read the flag?** The record shows the flag existed and the approval
  happened, not whether anyone looked. It can only prove the warning was on the record.
- **What did the note say?** The owner's timeline deliberately hides args and titles. The
  investigation needed a database administrator for that step.
- **Who uploaded the document?** Not on the timeline (RR-W10-5). It is in stderr, and since
  this replay on the upload request's root span. A real gap, and the first question an
  investigator asks.
- **What did each model call cost?** `model_calls` has one row per turn with tokens summed
  (3,716 in, 888 out). Only the spans split the two calls.
- **Order on the timeline.** The `model_call` row sorts after the tool rows, because it is
  written when the turn ends. Span times give the real order.

## What this walkthrough changed

**The first version of the rules missed this incident.** The replay's findings section
said `none`, on a run where a flagged phishing link had just been approved and stored.
Every fact needed was already in the record. The `approved_document_link` rule was added
after that, and [`detect-same-incident.txt`](detect-same-incident.txt) shows it firing on
this same row with no new model call. A clean dashboard had been answering "normal".

## Could this record have been faked?

Before this week, yes. [`forge-run-1-before.txt`](forge-run-1-before.txt): a user's own
token rewrote an executed call to `denied`, invented a model call, marked an action
executed without running it, and wrote into another org's correlation id. 4/4 landed. So
this walkthrough could not have been trusted.

After migration 0013, [`forge-run-2-after.txt`](forge-run-2-after.txt): 0/5 landed. A
database administrator or a compromised API can still rewrite rows (RR-W10-1). The spans
in the trace store are a second copy that such a rewrite would not reach, and that is the
only tamper check today. The trace store is local and in memory.
