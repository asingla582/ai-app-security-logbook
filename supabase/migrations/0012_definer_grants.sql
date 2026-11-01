-- 0012: close RR-W8-4.
-- Postgres grants EXECUTE on new functions to PUBLIC, and Supabase's default ACLs
-- also grant it to anon directly, so `revoke ... from public` alone left every
-- SECURITY DEFINER function callable without a login. Revoke anon explicitly on
-- each exposed function. The helpers (is_org_member etc.) had only the implicit
-- PUBLIC grant, which anon inherits, so they are revoked from public too, then
-- granted back to authenticated (RLS policies run as it) and service_role. tests/rls/test_definer_grants.py
-- checks the whole catalog so a future function cannot regress this silently.
--
-- record_model_call also trusted a caller-supplied org id; it now requires the
-- caller to be a member of that org.

revoke all on function count_org_model_calls_1d(uuid) from public, anon;
grant execute on function count_org_model_calls_1d(uuid) to authenticated, service_role;
revoke all on function count_user_tool_calls_1m() from public, anon;
grant execute on function count_user_tool_calls_1m() to authenticated, service_role;
revoke all on function create_organization(text) from public, anon;
grant execute on function create_organization(text) to authenticated, service_role;
revoke all on function is_org_member(uuid) from public, anon;
grant execute on function is_org_member(uuid) to authenticated, service_role;
revoke all on function is_org_owner(uuid) from public, anon;
grant execute on function is_org_owner(uuid) to authenticated, service_role;
revoke all on function owns_conversation(uuid) from public, anon;
grant execute on function owns_conversation(uuid) to authenticated, service_role;

create or replace function record_model_call(
  p_correlation_id text,
  p_org_id uuid,
  p_conversation_id uuid,
  p_model text,
  p_prompt text,
  p_redacted_input text,
  p_redacted_output text,
  p_input_tokens int,
  p_output_tokens int,
  p_sources jsonb,
  p_context_provenance jsonb,
  p_output_handling jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not is_org_member(p_org_id) then
    raise exception 'cannot record a call for an organization you do not belong to';
  end if;
  if p_conversation_id is not null and not owns_conversation(p_conversation_id) then
    raise exception 'cannot record a call for a conversation you do not own';
  end if;
  insert into model_calls (
    correlation_id, user_id, org_id, conversation_id, model, prompt,
    redacted_input, redacted_output, input_tokens, output_tokens, sources,
    context_provenance, output_handling
  ) values (
    p_correlation_id, auth.uid(), p_org_id, p_conversation_id, p_model, p_prompt,
    p_redacted_input, p_redacted_output, p_input_tokens, p_output_tokens, p_sources,
    p_context_provenance, p_output_handling
  );
end;
$$;

revoke all on function record_model_call(text, uuid, uuid, text, text, text, text, int, int, jsonb, jsonb, jsonb) from public, anon;
grant execute on function record_model_call(text, uuid, uuid, text, text, text, text, int, int, jsonb, jsonb, jsonb) to authenticated;
