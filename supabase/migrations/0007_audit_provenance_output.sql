-- Week 6: close the lineage loop. 0006 recorded which documents fed a call;
-- this records what trust tier every context element carried (provenance) and
-- what the output sanitizer did to the reply (output_handling). Together an
-- audit row answers, after the fact: which chunk carried a payload, what trust
-- it was given, and whether anything tried to leave through the answer -- a
-- stripped exfiltration link is a detection signal, so it must be on the record.

alter table model_calls add column context_provenance jsonb not null default '[]'::jsonb;
alter table model_calls add column output_handling jsonb not null default '{}'::jsonb;

drop function record_model_call(text, uuid, uuid, text, text, text, text, int, int, jsonb);

create function record_model_call(
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

revoke all on function record_model_call(text, uuid, uuid, text, text, text, text, int, int, jsonb, jsonb, jsonb) from public;
grant execute on function record_model_call(text, uuid, uuid, text, text, text, text, int, int, jsonb, jsonb, jsonb) to authenticated;
