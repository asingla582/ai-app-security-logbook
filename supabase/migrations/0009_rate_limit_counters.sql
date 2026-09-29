-- Week 7 fix: the rate-limit checks count from the audit tables (model_calls,
-- tool_calls), but those are not granted to the authenticated role and carry RLS
-- with no policy for it -- so a count(*) run on the caller's connection always
-- returned 0 and the limits never engaged. Count via SECURITY DEFINER so the
-- checks see the real rows. The caller passes an org_id it already owns (the
-- conversation's tenant) and its own user_id, so this discloses only counts the
-- caller is entitled to rate-limit against.

create function count_org_model_calls_1d(p_org_id uuid) returns bigint
language sql security definer set search_path = public as $$
  select count(*) from model_calls
  where org_id = p_org_id and created_at > now() - interval '1 day';
$$;

create function count_user_tool_calls_1m(p_user_id uuid) returns bigint
language sql security definer set search_path = public as $$
  select count(*) from tool_calls
  where user_id = p_user_id and proposed_at > now() - interval '1 minute';
$$;

revoke all on function count_org_model_calls_1d(uuid) from public;
grant execute on function count_org_model_calls_1d(uuid) to authenticated;
revoke all on function count_user_tool_calls_1m(uuid) from public;
grant execute on function count_user_tool_calls_1m(uuid) to authenticated;
