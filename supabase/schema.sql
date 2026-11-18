-- CUBIC's shared data for the Vercel copy, on Supabase.
--
-- One table of JSON documents with the same paths the page uses in Claude:
-- faces/f0, items/<id>, people/<account>, clock/<account>, chat/<id>, team/<who>,
-- seen/<account>, meta/<name>. Only accounts listed in `members` may read or write.
-- Nothing is removed: a delete marks the row `deleted`, so every open page learns
-- about it and the data can be brought back.
--
-- Run it in the SQL editor (or through the Management API). It can be run again.

-- Who is on the team: an account must be listed here before it can see anything.
create table if not exists public.members (
  user_id uuid primary key references auth.users (id) on delete cascade,
  note text,
  added_at timestamptz not null default now()
);
alter table public.members enable row level security;
revoke all on public.members from anon, authenticated;

create or replace function public.is_member() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce(((select auth.jwt()) ->> 'is_anonymous')::boolean, false) = false
     and exists (select 1 from public.members m where m.user_id = (select auth.uid()));
$$;
revoke all on function public.is_member() from public, anon;
grant execute on function public.is_member() to authenticated;

-- The documents.
create table if not exists public.docs (
  path text primary key
    check (path ~ '^(faces|items|people|clock|chat|team|seen|meta)/[A-Za-z0-9_.-]{1,128}$'),
  col text generated always as (split_part(path, '/', 1)) stored,
  data jsonb not null default '{}'::jsonb
    check (jsonb_typeof(data) = 'object' and octet_length(data::text) <= 262144),
  deleted boolean not null default false,
  version bigint not null default 1,
  updated_at timestamptz not null default clock_timestamp(),
  updated_by uuid
);
create index if not exists docs_updated_at_idx on public.docs (updated_at);
alter table public.docs enable row level security;

-- Every write is stamped by the server: when, by whom, and a version that only goes up.
create or replace function public.docs_stamp() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at := clock_timestamp();
  new.updated_by := (select auth.uid());
  new.version := case when tg_op = 'UPDATE' then old.version + 1 else 1 end;
  return new;
end $$;
drop trigger if exists docs_stamp on public.docs;
create trigger docs_stamp before insert or update on public.docs
  for each row execute function public.docs_stamp();

drop policy if exists docs_read on public.docs;
create policy docs_read on public.docs for select to authenticated
  using ((select public.is_member()));
drop policy if exists docs_insert on public.docs;
create policy docs_insert on public.docs for insert to authenticated
  with check ((select public.is_member()));
drop policy if exists docs_update on public.docs;
create policy docs_update on public.docs for update to authenticated
  using ((select public.is_member())) with check ((select public.is_member()));
-- No delete policy: the page never removes a row.

revoke all on public.docs from anon, authenticated;
grant select on public.docs to authenticated;

-- The page writes only through these three functions: signed-in accounts may read the
-- table (if they are members) but not insert or update it directly. Each function checks
-- membership itself and says why it refused.
create or replace function public.doc_set(p_path text, p_data jsonb) returns public.docs
language plpgsql security definer set search_path = '' as $$
declare r public.docs;
begin
  if not (select public.is_member()) then
    raise exception 'not a member' using errcode = '42501';
  end if;
  insert into public.docs as d (path, data, deleted) values (p_path, p_data, false)
  on conflict (path) do update set data = excluded.data, deleted = false
  returning d.* into r;
  return r;
end $$;

create or replace function public.doc_update(p_path text, p_patch jsonb) returns public.docs
language plpgsql security definer set search_path = '' as $$
declare r public.docs;
begin
  if not (select public.is_member()) then
    raise exception 'not a member' using errcode = '42501';
  end if;
  if jsonb_typeof(p_patch) is distinct from 'object' then
    raise exception 'patch must be an object' using errcode = '22023';
  end if;
  update public.docs set data = data || p_patch
    where path = p_path and not deleted returning * into r;
  if not found then
    raise exception 'no such document' using errcode = 'P0002';
  end if;
  return r;
end $$;

create or replace function public.doc_delete(p_path text) returns public.docs
language plpgsql security definer set search_path = '' as $$
declare r public.docs;
begin
  if not (select public.is_member()) then
    raise exception 'not a member' using errcode = '42501';
  end if;
  update public.docs set deleted = true where path = p_path returning * into r;
  return r;
end $$;

revoke all on function public.doc_set(text, jsonb), public.doc_update(text, jsonb),
  public.doc_delete(text), public.docs_stamp() from public, anon;
grant execute on function public.doc_set(text, jsonb), public.doc_update(text, jsonb),
  public.doc_delete(text) to authenticated;
