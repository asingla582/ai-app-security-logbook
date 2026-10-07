-- Week 8: the human approval gate. A tool that requires approval is never executed
-- when the model proposes it; the app parks it here and the requester approves it
-- in a separate request. What executes is exactly this row: args are frozen by the
-- trigger, the hash is computed here (never client-supplied), and a claim is
-- single-use. authenticated may only SELECT its own rows; every write is a
-- SECURITY DEFINER function below.

alter table tool_calls drop constraint tool_calls_status_check;
alter table tool_calls add constraint tool_calls_status_check check (status in
  ('proposed','invalid','denied','rate_limited','executed','failed',
   'pending_approval','approved','expired'));

create table pending_actions (
  id uuid primary key default gen_random_uuid(),
  tool_call_id uuid not null references tool_calls(id),
  correlation_id text not null,
  requester uuid not null,
  org_id uuid not null,
  conversation_id uuid references conversations(id) on delete set null,
  tool_name text not null,
  args jsonb not null,                 -- exact args to execute; unredacted, owner-only
  args_sha256 text not null,
  flags jsonb not null default '{}'::jsonb,
  status text not null check (status in
    ('pending','approved','executed','failed','denied','expired')),
  result_ref uuid,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  decided_at timestamptz,
  executed_at timestamptz
);

alter table pending_actions enable row level security;
revoke all on pending_actions from anon, authenticated;
grant select on pending_actions to authenticated;
create policy pending_actions_owner_select on pending_actions
  for select to authenticated using (requester = auth.uid());

-- Immutable record: applies to every role, including the definer functions and the
-- table owner. Only conversation_id may change outside a transition, because
-- deleting a conversation nulls it (on delete set null) on rows of any status.
create function pending_actions_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'pending_actions rows are immutable records';
  end if;
  if (new.id, new.tool_call_id, new.correlation_id, new.requester, new.org_id,
      new.tool_name, new.args, new.args_sha256, new.flags, new.created_at, new.expires_at)
     is distinct from
     (old.id, old.tool_call_id, old.correlation_id, old.requester, old.org_id,
      old.tool_name, old.args, old.args_sha256, old.flags, old.created_at, old.expires_at) then
    raise exception 'pending_actions: locked column changed';
  end if;
  if new.status is distinct from old.status then
    if not ((old.status = 'pending' and new.status in ('approved','denied','expired'))
         or (old.status = 'approved' and new.status in ('executed','failed'))) then
      raise exception 'pending_actions: illegal transition % -> %', old.status, new.status;
    end if;
  elsif old.status in ('executed','failed','denied','expired')
    and (new.result_ref, new.decided_at, new.executed_at)
        is distinct from (old.result_ref, old.decided_at, old.executed_at) then
    raise exception 'pending_actions: terminal row changed';
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end; $$;

create trigger pending_actions_guard before update or delete on pending_actions
  for each row execute function pending_actions_guard();

create function create_pending_action(
  p_correlation_id text, p_org_id uuid, p_conversation_id uuid, p_tool_call_id uuid,
  p_tool_name text, p_args jsonb, p_flags jsonb
) returns table(id uuid, args_sha256 text, expires_at timestamptz)
language plpgsql security definer set search_path = public as $$
#variable_conflict use_column
begin
  if p_conversation_id is not null and not owns_conversation(p_conversation_id) then
    raise exception 'cannot create a pending action for a conversation you do not own';
  end if;
  if not exists (select 1 from memberships m where m.org_id = p_org_id and m.user_id = auth.uid()) then
    raise exception 'not a member of this organization';
  end if;
  if not exists (select 1 from tool_calls t where t.id = p_tool_call_id and t.user_id = auth.uid()) then
    raise exception 'unknown tool call';
  end if;
  return query
    insert into pending_actions as pa (tool_call_id, correlation_id, requester, org_id,
      conversation_id, tool_name, args, args_sha256, flags, status, expires_at)
    values (p_tool_call_id, p_correlation_id, auth.uid(), p_org_id, p_conversation_id,
      p_tool_name, p_args, encode(sha256(convert_to(p_args::text, 'UTF8')), 'hex'),
      p_flags, 'pending', now() + interval '15 minutes')
    returning pa.id, pa.args_sha256, pa.expires_at;
end; $$;

-- Single-use, requester-anchored, membership-checked claim. Outcomes are RETURNED,
-- not raised, so the 'expired' state change commits with the caller's transaction.
-- FOR UPDATE serializes concurrent claims: the loser re-reads 'approved' and gets
-- already_decided.
create function claim_pending_action(p_id uuid, p_args_sha256 text)
returns table(outcome text, tool_name text, args jsonb, org_id uuid, tool_call_id uuid)
language plpgsql security definer set search_path = public as $$
#variable_conflict use_column
declare r pending_actions%rowtype;
begin
  select * into r from pending_actions pa
    where pa.id = p_id and pa.requester = auth.uid() for update;
  if not found or not exists (
    select 1 from memberships m where m.org_id = r.org_id and m.user_id = auth.uid()
  ) then
    return query select 'not_found'::text, null::text, null::jsonb, null::uuid, null::uuid;
    return;
  end if;
  if r.status <> 'pending' then
    return query select 'already_decided'::text, null::text, null::jsonb, null::uuid, null::uuid;
    return;
  end if;
  if r.expires_at <= now() then
    update pending_actions set status = 'expired', decided_at = now() where id = p_id;
    update tool_calls set status = 'expired' where id = r.tool_call_id;
    return query select 'expired'::text, null::text, null::jsonb, null::uuid, null::uuid;
    return;
  end if;
  if r.args_sha256 <> p_args_sha256 then
    return query select 'hash_mismatch'::text, null::text, null::jsonb, null::uuid, null::uuid;
    return;
  end if;
  update pending_actions set status = 'approved', decided_at = now() where id = p_id;
  update tool_calls set status = 'approved' where id = r.tool_call_id;
  return query select 'claimed'::text, r.tool_name, r.args, r.org_id, r.tool_call_id;
end; $$;

create function complete_pending_action(
  p_id uuid, p_executed boolean, p_result_ref uuid, p_result_summary text
) returns void
language plpgsql security definer set search_path = public as $$
declare v_tool_call uuid;
begin
  update pending_actions set
    status = case when p_executed then 'executed' else 'failed' end,
    executed_at = case when p_executed then now() else null end,
    result_ref = p_result_ref
  where id = p_id and requester = auth.uid() and status = 'approved'
  returning tool_call_id into v_tool_call;
  if v_tool_call is not null then
    update tool_calls set
      status = case when p_executed then 'executed' else 'failed' end,
      result_summary = p_result_summary,
      executed_at = case when p_executed then now() else executed_at end
    where id = v_tool_call;
  end if;
end; $$;

create function deny_pending_action(p_id uuid) returns text
language plpgsql security definer set search_path = public as $$
declare r pending_actions%rowtype;
begin
  select * into r from pending_actions pa
    where pa.id = p_id and pa.requester = auth.uid() for update;
  if not found then return 'not_found'; end if;
  if r.status <> 'pending' then return 'already_decided'; end if;
  update pending_actions set status = 'denied', decided_at = now() where id = p_id;
  update tool_calls set status = 'denied' where id = r.tool_call_id;
  return 'denied';
end; $$;

create function expire_my_pending_actions() returns void
language plpgsql security definer set search_path = public as $$
begin
  with overdue as (
    update pending_actions set status = 'expired', decided_at = now()
    where requester = auth.uid() and status = 'pending' and expires_at <= now()
    returning tool_call_id
  )
  update tool_calls set status = 'expired' where id in (select tool_call_id from overdue);
end; $$;

revoke all on function create_pending_action(text, uuid, uuid, uuid, text, jsonb, jsonb) from public;
grant execute on function create_pending_action(text, uuid, uuid, uuid, text, jsonb, jsonb) to authenticated;
revoke all on function claim_pending_action(uuid, text) from public;
grant execute on function claim_pending_action(uuid, text) to authenticated;
revoke all on function complete_pending_action(uuid, boolean, uuid, text) from public;
grant execute on function complete_pending_action(uuid, boolean, uuid, text) to authenticated;
revoke all on function deny_pending_action(uuid) from public;
grant execute on function deny_pending_action(uuid) to authenticated;
revoke all on function expire_my_pending_actions() from public;
grant execute on function expire_my_pending_actions() to authenticated;
