-- Farm Plans table for DD Hunt Tracker
-- Apply in Supabase SQL Editor (or psql) against your project.

create table if not exists public.farm_plans (
  id bigint generated always as identity primary key,
  year int not null,
  location text not null,
  notes text not null default '',
  updated_by text,
  updated_at timestamptz not null default now(),
  unique (year, location)
);

alter table public.farm_plans enable row level security;

-- Match existing anon open policies used by hunts/wildlife in this project.
-- Drop first so re-running this migration is safe.
drop policy if exists "farm_plans_select" on public.farm_plans;
drop policy if exists "farm_plans_insert" on public.farm_plans;
drop policy if exists "farm_plans_update" on public.farm_plans;
drop policy if exists "farm_plans_delete" on public.farm_plans;

create policy "farm_plans_select" on public.farm_plans for select to anon, authenticated using (true);
create policy "farm_plans_insert" on public.farm_plans for insert to anon, authenticated with check (true);
create policy "farm_plans_update" on public.farm_plans for update to anon, authenticated using (true) with check (true);
create policy "farm_plans_delete" on public.farm_plans for delete to anon, authenticated using (true);
