-- Pumping tab tables (applied via Supabase migration "pumping_tables")
create table if not exists public.well_runs (
  id bigint generated always as identity primary key,
  well text not null check (well in ('Main','South Block')),
  on_date date, on_time time, off_date date, off_time time,
  notes text not null default '',
  created_by text, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.farm_rainfall (
  id bigint generated always as identity primary key,
  year int not null, month int not null check (month between 7 and 10),
  inches numeric(6,2) not null,
  updated_by text, updated_at timestamptz not null default now(),
  unique (year, month)
);
grant select, insert, update, delete on public.well_runs, public.farm_rainfall to anon, authenticated;
grant all on public.well_runs, public.farm_rainfall to service_role;
alter table public.well_runs enable row level security;
alter table public.farm_rainfall enable row level security;
-- open anon/authenticated policies matching farm_plans (select/insert/update/delete using true)
