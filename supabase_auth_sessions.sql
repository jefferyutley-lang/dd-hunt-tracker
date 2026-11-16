-- Stay-logged-in sessions for DD Hunt Tracker
-- Applied in Supabase; the app uses the anon key like other club tables.

create table if not exists public.auth_sessions (
  id bigint generated always as identity primary key,
  username text not null,
  token_hash text not null unique,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  last_seen_at timestamptz not null default now(),
  revoked boolean not null default false
);
create index if not exists auth_sessions_token_hash_idx on public.auth_sessions (token_hash);
create index if not exists auth_sessions_username_idx on public.auth_sessions (username);

grant select, insert, update, delete on public.auth_sessions to anon, authenticated;
grant all on public.auth_sessions to service_role;
grant usage, select on sequence public.auth_sessions_id_seq to anon, authenticated;
grant all on sequence public.auth_sessions_id_seq to service_role;

alter table public.auth_sessions enable row level security;

drop policy if exists auth_sessions_select on public.auth_sessions;
drop policy if exists auth_sessions_insert on public.auth_sessions;
drop policy if exists auth_sessions_update on public.auth_sessions;
drop policy if exists auth_sessions_delete on public.auth_sessions;

create policy auth_sessions_select on public.auth_sessions for select to anon, authenticated using (true);
create policy auth_sessions_insert on public.auth_sessions for insert to anon, authenticated with check (true);
create policy auth_sessions_update on public.auth_sessions for update to anon, authenticated using (true) with check (true);
create policy auth_sessions_delete on public.auth_sessions for delete to anon, authenticated using (true);
