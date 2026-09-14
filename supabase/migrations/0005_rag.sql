-- Week 5: secure RAG. Documents and their chunks are org-scoped, isolated by the
-- same RLS pattern as notes (is_org_member). Retrieval is a vector similarity search
-- that RLS constrains to the caller's org BEFORE ranking, so no crafted query can
-- reach another org's chunks. The database authorizes retrieval, never the model.

create extension if not exists vector;

create table documents (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  uploaded_by uuid not null references auth.users(id),
  filename text not null,
  -- Sensitivity is labeled at ingest (data classification before it enters the
  -- system); carried as provenance, not trusted from the model.
  sensitivity text not null default 'internal'
    check (sensitivity in ('public', 'internal', 'confidential')),
  created_at timestamptz not null default now()
);

create table document_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents(id) on delete cascade,
  -- org_id denormalized onto the chunk so RLS filters at the row level during the
  -- similarity scan, keeping the org boundary ahead of ranking.
  org_id uuid not null references organizations(id) on delete cascade,
  ordinal int not null,
  content text not null,
  -- OpenAI text-embedding-3-small dimensionality; see week5-embeddings decision.
  embedding vector(1536),
  created_at timestamptz not null default now()
);

-- Approximate-nearest-neighbor index for cosine distance. Does not affect the RLS
-- boundary (RLS is applied regardless of index use).
create index document_chunks_embedding_idx
  on document_chunks using hnsw (embedding vector_cosine_ops);

grant select, insert, update, delete on documents to authenticated;
grant select, insert, delete on document_chunks to authenticated;

alter table documents enable row level security;
alter table document_chunks enable row level security;

-- Documents: readable/writable only by members of the owning org.
create policy documents_select on documents
  for select using (is_org_member(org_id));
create policy documents_insert on documents
  for insert with check (is_org_member(org_id) and uploaded_by = auth.uid());
create policy documents_delete on documents
  for delete using (is_org_member(org_id));

-- Chunks: same org boundary. The select policy is what makes a similarity query
-- safe — a non-member sees zero candidate rows, crafted query or not.
create policy chunks_select on document_chunks
  for select using (is_org_member(org_id));
create policy chunks_insert on document_chunks
  for insert with check (is_org_member(org_id));
create policy chunks_delete on document_chunks
  for delete using (is_org_member(org_id));
