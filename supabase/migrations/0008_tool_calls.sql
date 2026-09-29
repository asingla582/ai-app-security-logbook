-- Week 7: tool-call trajectory. One row per model tool proposal, updated as the
-- app decides and executes it, so an audit row answers after the fact: what did
-- the model want to do, was it allowed, did it run. Mirrors model_calls: audit is
-- server/ops-facing (not granted to authenticated), redacted content only.
create table tool_calls (
  id uuid primary key default gen_random_uuid(),
  correlation_id text not null,
  user_id uuid not null default auth.uid(),
  org_id uuid not null,
  conversation_id uuid references conversations(id) on delete set null,
  tool_name text not null,
  args jsonb not null default '{}'::jsonb,          -- redacted before storage
  status text not null check (status in
    ('proposed','invalid','denied','rate_limited','executed','failed')),
  result_summary text,                               -- redacted before storage
  proposed_at timestamptz not null default now(),
  decided_at timestamptz,
  executed_at timestamptz
);

alter table tool_calls enable row level security;
-- No policy for authenticated: audit is written via SECURITY DEFINER, read by ops.

create function record_tool_proposal(
  p_correlation_id text, p_org_id uuid, p_conversation_id uuid,
  p_tool_name text, p_args jsonb, p_status text
) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_id uuid;
begin
  if p_conversation_id is not null and not owns_conversation(p_conversation_id) then
    raise exception 'cannot record a tool call for a conversation you do not own';
  end if;
  -- Stamp user_id explicitly from auth.uid(), matching record_model_call (0007),
  -- rather than leaning on the column default.
  insert into tool_calls (correlation_id, user_id, org_id, conversation_id, tool_name, args, status)
  values (p_correlation_id, auth.uid(), p_org_id, p_conversation_id, p_tool_name, p_args, p_status)
  returning id into v_id;
  return v_id;
end; $$;

create function finalize_tool_call(
  p_id uuid, p_status text, p_result_summary text, p_executed boolean
) returns void
language plpgsql security definer set search_path = public as $$
begin
  update tool_calls set
    status = p_status,
    result_summary = p_result_summary,
    decided_at = coalesce(decided_at, now()),
    executed_at = case when p_executed then now() else executed_at end
  where id = p_id and user_id = auth.uid();
end; $$;

revoke all on function record_tool_proposal(text, uuid, uuid, text, jsonb, text) from public;
grant execute on function record_tool_proposal(text, uuid, uuid, text, jsonb, text) to authenticated;
revoke all on function finalize_tool_call(uuid, text, text, boolean) from public;
grant execute on function finalize_tool_call(uuid, text, text, boolean) to authenticated;
