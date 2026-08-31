-- Week 3: instruction security. Every audit row records which versioned prompt
-- template served the call (name.vN@content-hash), so any past response can be
-- traced to the exact instruction text that produced it.

alter table model_calls add column prompt text not null default '';

drop function record_model_call(text, uuid, uuid, text, text, text, int, int);

create function record_model_call(
  p_correlation_id text,
  p_org_id uuid,
  p_conversation_id uuid,
  p_model text,
  p_prompt text,
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
    correlation_id, user_id, org_id, conversation_id, model, prompt,
    redacted_input, redacted_output, input_tokens, output_tokens
  ) values (
    p_correlation_id, auth.uid(), p_org_id, p_conversation_id, p_model, p_prompt,
    p_redacted_input, p_redacted_output, p_input_tokens, p_output_tokens
  );
end;
$$;

revoke all on function record_model_call(text, uuid, uuid, text, text, text, text, int, int) from public;
grant execute on function record_model_call(text, uuid, uuid, text, text, text, text, int, int) to authenticated;
