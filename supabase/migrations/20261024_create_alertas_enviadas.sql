-- Historial central de alertas críticas (95-99 %).
-- Aplicar una sola vez en el proyecto Supabase usado por GitHub Actions.
-- La service_role key del monitor evita depender de políticas públicas.
create table if not exists public.alertas_enviadas (
  clave text primary key,
  tienda text not null default '',
  producto text not null default '',
  precio numeric,
  precio_anterior numeric,
  descuento integer not null check (descuento between 0 and 100),
  url text not null default '',
  created_at timestamptz not null default now()
);

create index if not exists alertas_enviadas_created_at_idx
  on public.alertas_enviadas (created_at desc);

alter table public.alertas_enviadas enable row level security;
revoke all on table public.alertas_enviadas from anon, authenticated;
grant all on table public.alertas_enviadas to service_role;
