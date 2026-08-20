# Week 2 Evidence — Chat, Audit, and a Red-Team

The claim: a user can chat with the assistant, every model call is recorded in an
audit log that holds no raw PII, and one user cannot reach another's conversation.
This directory is the proof, including where the claim is only partly true.

## What was attacked

Two users, Alice and Bob, on the running app against the real Claude model. Seven
attacks (`redteam.py`; captured run in `redteam-run.txt`):

| # | Attack | Result | Whose defense |
|---|--------|--------|---------------|
| 1 | Alice reads Bob's conversation (IDOR) | 404, blocked | ours (RLS + ownership) |
| 2 | Alice posts into Bob's conversation (IDOR) | 404, blocked | ours (RLS + ownership) |
| 3 | Prompt injection to leak the system prompt | refused | the model's alignment |
| 4 | Jailbreak to exfiltrate other users' data | refused | ours (architectural) |
| 5 | Role injection (extra `role` field in the body) | ignored, 201 | ours (server-set role) |
| 6 | PII (email/SSN/card) into the audit log | redacted | ours (redaction) |
| 7 | PII redaction evasion (obfuscated email) | **leaked** | — |

## What held

- **Tenant isolation.** Reading or writing another user's conversation returns 404
  at the API, and the database refuses the rows independently (`tests/rls/`). Blocked
  requests never reach the model, so they generate no audit entry.
- **Cross-tenant exfiltration is architectural, not luck.** Even a fully jailbroken
  model cannot read another tenant's data because it has no tools and no retrieval —
  it only ever sees the current user's own messages. This defense does not depend on
  the model behaving.
- **The audit log is not user-readable.** `model_calls` has no grant and no RLS policy
  for the `authenticated` role; only the server path (a `SECURITY DEFINER` function)
  writes it, stamping `user_id` from `auth.uid()` so rows cannot be forged.

## What broke

**PII redaction is best-effort (RR-W2-2), demonstrated live.** Sending
`my social is 123 45 6789 and reach me at mallory [at] evil [dot] com`, the audit log
stored:

```
my social is [SSN] and reach me at mallory [at] evil [dot] com
```

The SSN was masked; the obfuscated email walked straight through, because it is not a
standard `@`-shaped address. The audit log — the one place promised to hold no raw PII
— now contains a personal email. Redaction reduces PII exposure; it does not eliminate
it. A normally-written email is caught; a slightly non-standard one is not.

## Honest notes

- Attacks 3 and 7 (system-prompt leak, output exfil) were stopped by **Claude's own
  alignment, not by our code.** We have no instruction-security defense (Week 3) or
  output-handling defense (Week 6) yet. These are not counted as our defenses.
- **Denial-of-wallet is still open:** signup is unrestricted and there is no rate
  limiting, so anyone can register and drive paid model calls. Scheduled for Week 7.

## Reproduce it

Servers up and `.env` loaded (chat needs `ANTHROPIC_API_KEY`):

```
set -a; . ./.env; set +a
cd apps/api && . .venv/bin/activate
python ../../evidence/week2/redteam.py        # REDTEAM_PAUSE=3 to slow for recording
```

The redaction miss is also provable without the model: the CI attack suite
(`apps/api/tests/test_chat_attacks.py`, `apps/api/tests/test_redaction.py`) asserts
which formats are caught and which are documented to slip through.
