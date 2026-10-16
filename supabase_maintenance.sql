-- Maintenance tab tables (applied via Supabase migration "maintenance_tables")
create table if not exists public.assets (
  id bigint generated always as identity primary key,
  name text not null unique,
  model_year int,
  meter_unit text check (meter_unit in ('hours','miles')),
  active boolean not null default true,
  notes text not null default '',
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.maintenance_log (
  id bigint generated always as identity primary key,
  asset_id bigint not null references public.assets(id),
  service_date date not null,
  service_type text not null check (service_type in ('Oil change','Filters','Tires','Battery','Belt','Grease/lube','Inspection','Repair','Winterize','Other')),
  meter_reading numeric(10,1),
  cost numeric(10,2),
  done_by text,
  notes text not null default '',
  created_by text, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index if not exists maintenance_log_asset_date_idx on public.maintenance_log (asset_id, service_date desc);
insert into public.assets (name, model_year, meter_unit) values
  ('Yukon', null, 'miles'),
  ('Power Unit', null, 'hours'),
  ('Big Can-Am', 2023, 'hours'),
  ('Small Can-Am', 2025, 'hours'),
  ('Boat Motor', null, 'hours')
on conflict (name) do nothing;
grant select, insert, update, delete on public.assets, public.maintenance_log to anon, authenticated;
grant all on public.assets, public.maintenance_log to service_role;
alter table public.assets enable row level security;
alter table public.maintenance_log enable row level security;
create policy assets_select on public.assets for select to anon, authenticated using (true);
create policy assets_insert on public.assets for insert to anon, authenticated with check (true);
create policy assets_update on public.assets for update to anon, authenticated using (true) with check (true);
create policy assets_delete on public.assets for delete to anon, authenticated using (true);
create policy maintenance_log_select on public.maintenance_log for select to anon, authenticated using (true);
create policy maintenance_log_insert on public.maintenance_log for insert to anon, authenticated with check (true);
create policy maintenance_log_update on public.maintenance_log for update to anon, authenticated using (true) with check (true);
create policy maintenance_log_delete on public.maintenance_log for delete to anon, authenticated using (true);
-- Cost of ownership (migration "assets_purchase_cols")
alter table public.assets add column if not exists purchase_date date, add column if not exists purchase_price numeric(12,2);
