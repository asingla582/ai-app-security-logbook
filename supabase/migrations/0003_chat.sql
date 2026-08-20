-- Week 2: chat. Conversations are user-scoped (private to the creator), not org-scoped.

create table conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  org_id uuid not null references organizations(id) on delete cascade,
  title text not null default 'New conversation',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamptz not null default now()
);

-- Audit log: redacted content only, so it survives conversation deletion
-- (conversation_id set null) without keeping raw PII.
create table model_calls (
  id uuid primary key default gen_random_uuid(),
  correlation_id text,
  user_id uuid,
  org_id uuid,
  conversation_id uuid references conversations(id) on delete set null,
  model text not null,
  redacted_input text not null,
  redacted_output text not null,
  input_tokens int not null default 0,
  output_tokens int not null default 0,
  created_at timestamptz not null default now()
);

-- security definer avoids recursion through messages' own RLS.
create function owns_conversation(target_conversation uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from conversations
    where id = target_conversation and user_id = auth.uid()
  );
$$;

grant select, insert, update, delete on conversations to authenticated;
grant select, insert on messages to authenticated;

alter table conversations enable row level security;
alter table messages enable row level security;
alter table model_calls enable row level security;

create policy conv_select on conversations for select using (user_id = auth.uid());
create policy conv_insert on conversations for insert with check (user_id = auth.uid());
create policy conv_update on conversations for update using (user_id = auth.uid());
create policy conv_delete on conversations for delete using (user_id = auth.uid());

create policy msg_select on messages for select using (owns_conversation(conversation_id));
create policy msg_insert on messages for insert with check (owns_conversation(conversation_id));

-- model_calls has no grants/policy for authenticated: users can't read or write it.
-- Writes go only through this function, which stamps user_id from auth.uid() (no forging).
create function record_model_call(
  p_correlation_id text,
  p_org_id uuid,
  p_conversation_id uuid,
  p_model text,
  p_redacted_input text,
  p_redacted_output text,
  p_input_tokens int,
  p_output_tokens int
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_conversation_id is not null and not owns_conversation(p_conversation_id) then
    raise exception 'cannot record a call for a conversation you do not own';
  end if;
  insert into model_calls (
    correlation_id, user_id, org_id, conversation_id, model,
    redacted_input, redacted_output, input_tokens, output_tokens
  ) values (
    p_correlation_id, auth.uid(), p_org_id, p_conversation_id, p_model,
    p_redacted_input, p_redacted_output, p_input_tokens, p_output_tokens
  );
end;
$$;

revoke all on function record_model_call(text, uuid, uuid, text, text, text, int, int) from public;
grant execute on function record_model_call(text, uuid, uuid, text, text, text, int, int) to authenticated;
