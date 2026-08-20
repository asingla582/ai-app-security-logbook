-- Week 1 hardening: the original membership_insert let any user grant themselves
-- into any org. Restrict membership inserts to existing owners; loosen nothing.
create function is_org_owner(target_org uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from memberships
    where org_id = target_org and user_id = auth.uid() and role = 'owner'
  );
$$;

drop policy membership_insert on memberships;
create policy membership_insert on memberships
  for insert with check (is_org_owner(org_id));

-- The first owner can't already be an owner, so org creation moves into a security
-- definer function; direct org inserts are dropped, making it the only entry point.
drop policy org_insert on organizations;

create function create_organization(org_name text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_org uuid;
  caller uuid := auth.uid();
begin
  if caller is null then
    raise exception 'not authenticated';
  end if;
  insert into organizations (name) values (org_name) returning id into new_org;
  insert into memberships (user_id, org_id, role) values (caller, new_org, 'owner');
  return new_org;
end;
$$;

revoke all on function create_organization(text) from public;
grant execute on function create_organization(text) to authenticated;
