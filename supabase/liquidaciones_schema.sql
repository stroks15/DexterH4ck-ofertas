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
