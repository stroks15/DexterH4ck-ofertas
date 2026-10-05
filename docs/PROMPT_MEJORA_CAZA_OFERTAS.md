# Prompt maestro — Auditoría y mejora del motor de ofertas

Actúa como Ingeniero de Software Senior especializado en Python, Web Scraping resiliente, APIs públicas, datos estructurados y arquitectura de conectores para e-commerce mexicano.

## Objetivo
Audita y mejora `stroks15/DexterH4ck-ofertas` sin romper el flujo actual. Aumenta el descubrimiento real de productos y liquidaciones, evita falsos descuentos y conserva enlaces directos de producto.

No inventes endpoints internos, campos GraphQL, credenciales ni contratos. Cuando una API interna no sea pública/documentada, trátala como configurable y usa como fallback datos públicos/JSON-LD/Next data/indexación legítima. No intentes saltar CAPTCHA, Cloudflare, WAF, rate limits ni controles de acceso.

## Reglas de negocio
- Descuentos comparables publicables: 50–99%.
- Liquidaciones por terminación física: Walmart/Bodega .01/.02/.03; Soriana según las señales ya soportadas.
- El precio de referencia actual de la ficha tiene prioridad sobre el historial.
- El historial sirve como respaldo cuando la ficha actual no trae referencia.
- Nunca inventar precio anterior.
- La oferta verde requiere precio actual + referencia válida + descuento.
- Liquidación/ocasión sin referencia debe distinguirse del formato verde.
- Deduplicar por identificador estable y no por precio.
- Mantener `id`, `nombre/titulo`, `marca`, `precio_actual`, `precio_anterior`, `descuento`, `url`, `score/puntuacion`, `es_bomba`, `tienda`.

## Identificadores
1. Walmart/Bodega: extraer `/ip/<slug>/<UPC>` y `wl13` como `store_id`. Si existe un endpoint GraphQL autorizado/configurado, usar una operación real observada en el sitio; `SearchAndFilter` o `getProductsByIds` solo si el esquema real lo confirma. Nunca fabricar un esquema GraphQL.
2. Coppel: desenvolver `dl` con `urllib.parse`, extraer SKU de `/pdp/...-pr-<SKU>`, y si la ficha responde, leer `__NEXT_DATA__`/datos estructurados. Ante 403, registrar bloqueo y no evadirlo.
3. Amazon: extraer ASIN de `/dp/<10 caracteres>` y `/gp/product/<ASIN>`; no asumir API pública inexistente.
4. Mercado Libre: expandir enlaces cortos con HEAD + redirects, extraer `MLM...`, usar API oficial como enriquecimiento. Desde octubre de 2026 preferir `/items/bulk?ids=...` para multiconsulta y `body.` en selección de campos. No exigir token ni `official_store_id` para descubrimiento público.

## Arquitectura
- Cada tienda falla de forma aislada.
- Prioridad: API > datos estructurados > HTML público > índice público.
- 403/429/5xx activa circuit breaker/backoff y no convierte toda la ejecución en cero candidatos.
- Jitter y Retry-After.
- Deduplicar tras combinar fuentes conservando el candidato de mayor calidad.
- Métricas por tienda: encontrados, con precio, con referencia, descartados, motivo y publicables.

## Validación
1. Compila Python.
2. Ejecuta todos los tests.
3. Añade pruebas de identificadores.
4. Ejecuta GitHub Actions.
5. Revisa logs y artefactos.
6. Corrige fallos y vuelve a ejecutar.
7. No declares una tienda funcional solo por HTTP 200: demuestra candidatos o explica el bloqueo externo.

## Entregable
Realiza directamente los cambios en el repositorio y resume causa raíz, archivos modificados, pruebas, resultado de Actions, tiendas con candidatos y bloqueos/fallbacks externos.
