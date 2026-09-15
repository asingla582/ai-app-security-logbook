# Week 5 Evidence — Secure RAG (retrieval authorized by the database, not the model)

The claim: the assistant can read an organization's documents and answer from them,
and one organization can never retrieve another's, no matter how a query is worded.
The database authorizes retrieval before the model sees anything. This directory is
the proof.

## The design in one line

Documents and their chunks are org-scoped. Retrieval is a vector similarity search
that Row Level Security constrains to the caller's org *before* ranking, plus an
explicit `org_id` filter on the conversation's org. The model phrases the answer; it
never chooses what it is allowed to read.

## What was attacked

The central attack for this week is cross-tenant retrieval: get one org's assistant
to surface another org's documents.

1. **Crafted query against the vector index (DB layer).** A similarity query whose
   vector is tuned to exactly match another tenant's chunk still returns only the
   caller's rows, because RLS filters candidates before ranking.
   `tests/rls/test_rag_isolation.py::test_crafted_query_cannot_retrieve_across_tenants`.
2. **Cross-tenant retrieval through chat (API layer).** Bob uploads a document with a
   distinctive canary. Alice, in her own org, asks a question engineered to match it.
   Her answer cites only her org's documents; none of Bob's document ids appear.
   `apps/api/tests/test_rag_chat.py::test_alice_chat_cannot_retrieve_bob_documents`.
3. **Cross-tenant upload / list.** Alice cannot upload into, or list, Bob's org.
   `apps/api/tests/test_documents.py`.

## What held

- **Zero cross-tenant retrieval**, at the database and through the chat path. The
  boundary is RLS (`is_org_member`) plus an explicit org filter, both independent of
  the model. Proven with no model in the loop.
- **Retrieval is scoped before ranking.** The crafted-vector test is the important
  one: even a query that *would* rank another org's chunk first cannot see it.
- **Lineage recorded.** Every answer records the document ids it drew from in the
  `model_calls` audit row (`sources`), so any output traces to its prompt and its
  sources (`test_rag_chat.py::test_chat_records_source_lineage_in_audit`).

## What is deferred (stated, not hidden)

- **Indirect prompt injection is NOT defended yet.** Retrieved document text enters
  the user/data channel as a labeled block, so it is structurally not an instruction,
  but a malicious document could still try to steer the answer. Neutralizing that,
  plus provenance/trust labels and output-side exfiltration defenses, is **Week 6**.
- **Embedding data-egress.** Document text is sent to OpenAI at ingest to produce
  embeddings (the chosen provider). This is a real third-party egress, recorded in
  the [threat model](../../security/threat-model.md). Tests and CI use a deterministic
  offline fake, so they need no key and send nothing.
- **Retrieval quality** (hybrid BM25 + rerank) was cut; this week is about
  authorization, not answer quality. If added later, authorization applies identically
  to every retrieval path so the sparse path cannot become a bypass.

## Reproduce it

```
make setup                       # local Supabase + venv (no OpenAI key needed for tests)
set -a; . ./.env; set +a
. apps/api/.venv/bin/activate
python -m pytest -q apps/api/tests/test_documents.py apps/api/tests/test_rag_chat.py tests/rls/test_rag_isolation.py
```

To run retrieval against real embeddings instead of the fake, add `OPENAI_API_KEY`
to `.env`. It is not required for the tests above.
