-- Week 2: chat. Conversations are private to the individual user (not org-shared),
-- so the isolation model here is user-scoped, unlike Week 1's org-scoped tables.

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

-- Audit log of every model call. Holds only redacted content, never raw PII, so
-- it can outlive the conversation it describes. conversation_id is nullable and
-- set null on delete: deleting a conversation removes the raw messages but keeps
-- the redacted audit trail for traceability. This reconciles "deletion actually
-- deletes" (raw content) with keeping an audit record (redacted only).
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

-- security definer so message policies can check conversation ownership without
-- recursing through the messages table's own RLS
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

-- Conversations: only the owner sees or touches them. A just-created conversation
-- is immediately visible to its creator (user_id = auth.uid()), so INSERT...RETURNING
-- is safe here, unlike the Week 1 org-creation case.
create policy conv_select on conversations for select using (user_id = auth.uid());
create policy conv_insert on conversations for insert with check (user_id = auth.uid());
create policy conv_update on conversations for update using (user_id = auth.uid());
create policy conv_delete on conversations for delete using (user_id = auth.uid());

create policy msg_select on messages for select using (owns_conversation(conversation_id));
create policy msg_insert on messages for insert with check (owns_conversation(conversation_id));

-- model_calls has RLS enabled and NO policy for authenticated, and is granted to no
-- one: end users cannot read or write the audit log directly. Writes happen only
-- through the security definer function below, which stamps user_id from auth.uid()
-- so an audit row cannot be forged for another user.
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
