-- 0013: Week 10. The audit trail is evidence, so the person it describes cannot be
-- able to write it. A signed-in user could reach every audit writer through the
-- database API with their own token (evidence/week10/forge-run-1-before.txt).
--
-- audit_writer is a NOLOGIN role holding EXECUTE on the writers. The API connects as
-- postgres and switches to it around each write (app/db.py). postgres on Supabase is
-- not a superuser, so it needs the explicit grant. authenticated and authenticator
-- are not members, so nothing reaching the database as a user can switch to it.

do $$ begin
  -- Roles are per cluster, not per database, so another database may already have it.
  if not exists (select 1 from pg_roles where rolname = 'audit_writer') then
    create role audit_writer nologin;
  end if;
end $$;
grant audit_writer to postgres;
grant usage on schema public to audit_writer;

-- RLS already blocks writes here, but TRUNCATE ignores RLS. Nothing should hold these.
revoke insert, update, delete, truncate, references, trigger
  on model_calls, tool_calls from public, anon, authenticated;

-- Links the approval request's trace back to the proposal's. Trace ids only.
alter table pending_actions add column trace_context text;

create or replace function pending_actions_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'pending_actions rows are immutable records';
  end if;
  if (new.id, new.tool_call_id, new.correlation_id, new.requester, new.org_id,
      new.tool_name, new.args, new.args_sha256, new.flags, new.created_at, new.expires_at,
      new.trace_context)
     is distinct from
     (old.id, old.tool_call_id, old.correlation_id, old.requester, old.org_id,
      old.tool_name, old.args, old.args_sha256, old.flags, old.created_at, old.expires_at,
      old.trace_context) then
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

drop function create_pending_action(text, uuid, uuid, uuid, text, jsonb, jsonb);
create function create_pending_action(
  p_correlation_id text, p_org_id uuid, p_conversation_id uuid, p_tool_call_id uuid,
  p_tool_name text, p_args jsonb, p_flags jsonb, p_trace_context text
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
      conversation_id, tool_name, args, args_sha256, flags, status, expires_at, trace_context)
    values (p_tool_call_id, p_correlation_id, auth.uid(), p_org_id, p_conversation_id,
      p_tool_name, p_args, encode(sha256(convert_to(p_args::text, 'UTF8')), 'hex'),
      p_flags, 'pending', now() + interval '15 minutes', p_trace_context)
    returning pa.id, pa.args_sha256, pa.expires_at;
end; $$;

-- Decisions that left no row: approve and deny outcomes, budget refusals.
create table audit_events (
  id uuid primary key default gen_random_uuid(),
  correlation_id text not null,
  user_id uuid not null,
  org_id uuid,
  action text not null,
  outcome text not null,
  subject_id text,
  created_at timestamptz not null default now()
);
create index audit_events_org_time on audit_events (org_id, created_at desc);
alter table audit_events enable row level security;
revoke all on audit_events from public, anon, authenticated;

create function record_audit_event(
  p_correlation_id text, p_org_id uuid, p_action text, p_outcome text, p_subject_id text
) returns void
language plpgsql security definer set search_path = public as $$
begin
  -- A member, or someone with their own action or conversation there. A revoked member
  -- still trying is exactly what the owner should see, so it must record.
  if p_org_id is not null and not is_org_member(p_org_id)
    and not exists (select 1 from pending_actions p
                    where p.org_id = p_org_id and p.requester = auth.uid())
    and not exists (select 1 from conversations c
                    where c.org_id = p_org_id and c.user_id = auth.uid()) then
    raise exception 'cannot record an event for an organization you do not belong to';
  end if;
  insert into audit_events (correlation_id, user_id, org_id, action, outcome, subject_id)
  values (p_correlation_id, auth.uid(), p_org_id, p_action, p_outcome, p_subject_id);
end; $$;

-- Every audit writer: the API only, never a user token.
do $$
declare sig text;
begin
  foreach sig in array array[
    'record_model_call(text, uuid, uuid, text, text, text, text, int, int, jsonb, jsonb, jsonb)',
    'record_tool_proposal(text, uuid, uuid, text, jsonb, text)',
    'finalize_tool_call(uuid, text, text, boolean)',
    'create_pending_action(text, uuid, uuid, uuid, text, jsonb, jsonb, text)',
    'claim_pending_action(uuid, text)',
    'complete_pending_action(uuid, boolean, uuid, text)',
    'deny_pending_action(uuid)',
    'expire_my_pending_actions()',
    'record_audit_event(text, uuid, text, text, text)'
  ] loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role', sig);
    execute format('grant execute on function %s to audit_writer', sig);
  end loop;
end $$;

-- Hosts only: a stripped URL can carry the data it tried to take, and it is stored
-- unredacted, so its path and query never leave this function.
create function url_hosts(p_urls jsonb) returns jsonb
language sql immutable set search_path = public as $$
  select coalesce(jsonb_agg(distinct coalesce(
    lower(substring(u from '^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^/?#\\@]*@)?([a-zA-Z0-9.-]+)(?::[0-9]+)?(?:[/?#]|$)')),
    'unparsed')), '[]'::jsonb)
  from jsonb_array_elements_text(
    case when jsonb_typeof(p_urls) = 'array' then p_urls else '[]'::jsonb end) as u
$$;
revoke all on function url_hosts(jsonb) from public, anon;
grant execute on function url_hosts(jsonb) to authenticated;

-- Org owner only. Ids, verdicts and counts: no reply text, note titles or action
-- args, because an owner reviewing decisions has no need to read members' chats.
create function audit_timeline(
  p_org_id uuid, p_correlation_id text default null,
  p_before timestamptz default null, p_limit int default 100
) returns table(ts timestamptz, kind text, correlation_id text, user_id uuid,
                action text, outcome text, subject_id text, detail jsonb)
language plpgsql stable security definer set search_path = public as $$
begin
  if not is_org_owner(p_org_id) then
    raise exception 'not an owner of this organization' using errcode = '42501';
  end if;
  return query
  select * from (
    select m.created_at, 'model_call'::text, m.correlation_id, m.user_id, m.prompt,
      case when m.redacted_output = '[model error]' then 'error' else 'ok' end, m.id::text,
      jsonb_build_object(
        'model', m.model, 'input_tokens', m.input_tokens, 'output_tokens', m.output_tokens,
        'source_count', case when jsonb_typeof(m.sources) = 'array' then jsonb_array_length(m.sources) else 0 end,
        'stripped_images', url_hosts(m.output_handling -> 'blocked_images'),
        'stripped_links', url_hosts(m.output_handling -> 'defanged_links'))
    from model_calls m where m.org_id = p_org_id
    union all
    -- left(): an unregistered tool name is model-written text, so it is cut short.
    select t.proposed_at, 'tool_call', t.correlation_id, t.user_id, left(t.tool_name, 64), t.status, t.id::text,
      jsonb_build_object('decided_at', t.decided_at, 'executed_at', t.executed_at)
    from tool_calls t where t.org_id = p_org_id
    union all
    -- Expiry is only written when the requester next lists actions, so derive it here.
    select p.created_at, 'approval', p.correlation_id, p.requester, p.tool_name,
      case when p.status = 'pending' and p.expires_at <= now() then 'expired' else p.status end,
      p.id::text,
      jsonb_build_object('args_sha256', p.args_sha256, 'decided_at', p.decided_at,
        'executed_at', p.executed_at,
        'flagged_hosts', jsonb_path_query_array(p.flags, '$.urls[*].host'))
    from pending_actions p where p.org_id = p_org_id
    union all
    select e.created_at, 'event', e.correlation_id, e.user_id, e.action, e.outcome, e.subject_id,
      '{}'::jsonb
    from audit_events e where e.org_id = p_org_id
  ) r(ts, kind, correlation_id, user_id, action, outcome, subject_id, detail)
  where (p_correlation_id is null or r.correlation_id = p_correlation_id)
    and (p_before is null or r.ts < p_before)
  order by r.ts desc
  limit least(greatest(coalesce(p_limit, 100), 1), 500);
end; $$;
revoke all on function audit_timeline(uuid, text, timestamptz, int) from public, anon;
grant execute on function audit_timeline(uuid, text, timestamptz, int) to authenticated;

-- "Normal or breach?" Fixed rules over the rows above. p_approval_tools comes from the
-- app's tool registry, so a newly gated tool is covered without a migration.
create function audit_anomalies(p_org_id uuid, p_approval_tools text[])
returns table(rule text, severity text, ts timestamptz, correlation_id text,
              user_id uuid, subject_id text, detail jsonb)
language plpgsql stable security definer set search_path = public as $$
begin
  if not is_org_owner(p_org_id) then
    raise exception 'not an owner of this organization' using errcode = '42501';
  end if;
  return query
  -- A gated tool ran with no executed approval behind it. Should be impossible.
  select 'execution_without_approval'::text, 'high'::text, t.proposed_at, t.correlation_id,
    t.user_id, t.id::text, jsonb_build_object('tool', left(t.tool_name, 64))
  from tool_calls t
  where t.org_id = p_org_id and t.status = 'executed' and t.tool_name = any(p_approval_tools)
    and not exists (select 1 from pending_actions p
                    where p.tool_call_id = t.id and p.status = 'executed')
  union all
  -- A person approved an action carrying a link that came out of a document, and it
  -- ran. Added after the Week 10 replay: this exact incident returned no findings.
  select 'approved_document_link', 'high', p.decided_at, p.correlation_id, p.requester, p.id::text,
    jsonb_build_object('tool', p.tool_name,
      'hosts', jsonb_path_query_array(p.flags, '$.urls[*] ? (@.source != "model").host'))
  from pending_actions p
  where p.org_id = p_org_id and p.status = 'executed'
    and jsonb_path_exists(p.flags, '$.urls[*] ? (@.source != "model")')
  union all
  -- The model tried to emit a link or image the sanitizer would not allow.
  select 'sanitizer_stripped_url', 'medium', m.created_at, m.correlation_id, m.user_id, m.id::text,
    jsonb_build_object('images', url_hosts(m.output_handling -> 'blocked_images'),
                       'links', url_hosts(m.output_handling -> 'defanged_links'))
  from model_calls m
  where m.org_id = p_org_id
    and (url_hosts(m.output_handling -> 'blocked_images') <> '[]'::jsonb
      or url_hosts(m.output_handling -> 'defanged_links') <> '[]'::jsonb)
  union all
  -- Repeated approvals with the wrong hash: someone is testing the gate.
  select 'approval_hash_probing', 'high', max(e.created_at), max(e.correlation_id), e.user_id, null,
    jsonb_build_object('count', count(*))
  from audit_events e
  where e.org_id = p_org_id and e.outcome = 'hash_mismatch'
  group by e.user_id, date_trunc('hour', e.created_at) having count(*) >= 3
  union all
  -- One user far above the fixed hourly ceiling. Not a baseline: a stated threshold.
  select 'model_call_volume', 'medium', max(m.created_at), max(m.correlation_id), m.user_id, null,
    jsonb_build_object('count', count(*))
  from model_calls m
  where m.org_id = p_org_id
  group by m.user_id, date_trunc('hour', m.created_at) having count(*) > 50
  order by 3 desc;
end; $$;
revoke all on function audit_anomalies(uuid, text[]) from public, anon;
grant execute on function audit_anomalies(uuid, text[]) to authenticated;
