# Integración Liquidaciones

## Origen

El proyecto Supabase `cremaciones-nextli` contiene las migraciones:

- `liquidaciones_init_schema`
- `liquidaciones_seed_stores`

Estas se integrarán como módulo independiente del bot de ofertas.

## Objetivo

Crear un motor de detección de liquidaciones para Telegram:

- Detectar productos con descuentos reales.
- Clasificar liquidaciones, remates y últimas piezas.
- Guardar historial para evitar duplicados.
- Enviar alertas al grupo Telegram.

## Arquitectura prevista

```
Supabase Ofertas
 |
 +-- tiendas
 +-- productos
 +-- liquidaciones
 +-- historial_alertas
 |
Bot Telegram
 |
Grupo de ofertas
```

## Reglas

- No usar terminaciones .01/.02/.03 como única prueba.
- Validar precio anterior y precio actual.
- Priorizar descuentos altos.
- Mantener trazabilidad de fuente.

## Próximo paso

Extraer el SQL real de las migraciones de Supabase y convertirlo en:

```
supabase/migrations/
  liquidaciones_init_schema.sql
  liquidaciones_seed_stores.sql
```
