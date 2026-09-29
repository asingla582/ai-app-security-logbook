-- Week 7 hardening (review follow-up to 0009): anchor the rate-limit counters to the
-- calling user. The counts are low-value (a bigint), and the routing code only ever
-- passes the caller's own conversation org and user, but the functions themselves
-- trusted whatever id they were given. Re-scope both to auth.uid() so a count can only
-- ever be produced for the caller's own user, or for an org the caller belongs to.

drop function count_org_model_calls_1d(uuid);
create function count_org_model_calls_1d(p_org_id uuid) returns bigint
language sql security definer set search_path = public as $$
  select count(*) from model_calls
  where org_id = p_org_id
    and created_at > now() - interval '1 day'
    and exists (select 1 from memberships where org_id = p_org_id and user_id = auth.uid());
$$;

drop function count_user_tool_calls_1m(uuid);
create function count_user_tool_calls_1m() returns bigint
language sql security definer set search_path = public as $$
  select count(*) from tool_calls
  where user_id = auth.uid() and proposed_at > now() - interval '1 minute';
$$;

revoke all on function count_org_model_calls_1d(uuid) from public;
grant execute on function count_org_model_calls_1d(uuid) to authenticated;
revoke all on function count_user_tool_calls_1m() from public;
grant execute on function count_user_tool_calls_1m() to authenticated;
