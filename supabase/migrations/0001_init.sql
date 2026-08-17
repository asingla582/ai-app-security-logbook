create table organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table memberships (
  user_id uuid not null references auth.users(id) on delete cascade,
  org_id uuid not null references organizations(id) on delete cascade,
  role text not null check (role in ('owner', 'member')),
  primary key (user_id, org_id)
);

create table notes (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  title text not null,
  body text not null default '',
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);

-- security definer so the policy can read memberships without triggering the
-- membership table's own RLS, which would recurse. The function is the single
-- authority for "is this caller in this org" and every policy routes through it.
create function is_org_member(target_org uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from memberships
    where org_id = target_org and user_id = auth.uid()
  );
$$;

alter table organizations enable row level security;
alter table memberships enable row level security;
alter table notes enable row level security;

create policy org_select on organizations
  for select using (is_org_member(id));

-- any authenticated user may create an org; the API adds their owner membership
-- in the same transaction, so a just-created org is immediately visible to them
create policy org_insert on organizations
  for insert with check (auth.uid() is not null);

create policy membership_select on memberships
  for select using (is_org_member(org_id));

create policy membership_insert on memberships
  for insert with check (auth.uid() is not null);

create policy notes_select on notes
  for select using (is_org_member(org_id));

-- a note can only be written into an org the caller belongs to, and can only be
-- attributed to the caller — a document cannot forge its own authorship
create policy notes_insert on notes
  for insert with check (is_org_member(org_id) and created_by = auth.uid());

create policy notes_update on notes
  for update using (is_org_member(org_id));

create policy notes_delete on notes
  for delete using (is_org_member(org_id));
