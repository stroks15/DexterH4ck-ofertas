-- Estructura independiente para ofertas y liquidaciones
-- Adaptada para DexterH4ck-ofertas

create table if not exists tiendas (
 id bigint generated always as identity primary key,
 nombre text unique not null,
 activo boolean default true
);

create table if not exists liquidaciones_detectadas (
 id bigint generated always as identity primary key,
 tienda text not null,
 producto text not null,
 precio numeric,
 precio_anterior numeric,
 descuento numeric,
 puntuacion numeric,
 url text,
 creado timestamp default now()
);

-- Historial de alertas 95-99 % ya enviadas (lo consulta core/supabase_history.py).
-- RLS activo y sin políticas: solo la clave de servicio (SUPABASE_SERVICE_ROLE_KEY) puede leer/escribir.
create table if not exists alertas_enviadas (
 clave text primary key,
 tienda text,
 producto text,
 precio numeric,
 precio_anterior numeric,
 descuento numeric,
 url text,
 enviado timestamptz default now()
);

alter table alertas_enviadas enable row level security;
