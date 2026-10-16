-- Farm Plans: field work log (applied via Supabase migration "farm_activities")
create table if not exists public.farm_activities (
  id bigint generated always as identity primary key,
  activity text not null check (activity in ('Planting','Coffee Bean Spraying','Brushing','Farm Disking')),
  activity_date date not null,
  year int generated always as (extract(year from activity_date)::int) stored,
  field text not null default '',
  notes text not null default '',
  created_by text, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index if not exists farm_activities_year_idx on public.farm_activities (year, activity);
grant select, insert, update, delete on public.farm_activities to anon, authenticated;
grant all on public.farm_activities to service_role;
alter table public.farm_activities enable row level security;
create policy farm_activities_select on public.farm_activities for select to anon, authenticated using (true);
create policy farm_activities_insert on public.farm_activities for insert to anon, authenticated with check (true);
create policy farm_activities_update on public.farm_activities for update to anon, authenticated using (true) with check (true);
create policy farm_activities_delete on public.farm_activities for delete to anon, authenticated using (true);
