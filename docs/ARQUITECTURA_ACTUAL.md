# Arquitectura actual de obtención de ofertas

## Contrato del pipeline

**Fuente → `scraper.discover()` → filas normalizadas → dedupe → publicación.**

1. `monitor_ofertas.definir_fuentes()` instancia `ApiStoresScraper`; cada adaptador se ejecuta aislado y expone `discover()`/`last_status`. En paralelo corren fuentes legacy (Scrapling/HTML, proxy, físicas y comunidad). Mercado Libre tiene su adaptador oficial separado.
2. Los adaptadores de `core.scraper_base.BaseScraper` devuelven listas de diccionarios sin publicar. El esquema común incluye `tienda`, `titulo`, `url`, `precio_actual`, `precio_anterior`, `origen_link`; los adaptadores agregan `tipo_fuente`, identificadores y descuento cuando están disponibles.
3. Las capas API/VTEX reportan estado por fuente y protegen precios con `core.price_sanitizer`; las fuentes HTML existentes reportan estado/cooldown por tienda. El preflight informa disponibilidad, no sustituye la obtención.
4. El monitor consolida resultados, normaliza identidad/tienda con `core.offer_identity`, deduplica candidatos entre fuentes y después aplica reglas de liquidación, historial y elegibilidad.
5. Solo el orquestador decide alertar/publicar (Telegram y persistencia); los scrapers nunca publican directamente.

## Restricciones de compatibilidad

- Un adaptador nuevo debe implementar `discover()` no-excepcional y `last_status`, devolver filas compatibles con el esquema anterior y coexistir con todos los adaptadores actuales.
- La obtención nueva se antepone por tienda y conserva el método existente como respaldo; Mercado Libre continúa por su API actual.
- El motor no cambia umbrales, dedupe, publicación ni esquema de Supabase. Con dependencias opcionales ausentes, el pipeline API/legacy sigue funcionando.
- No se inventan endpoints internos; solo rutas públicas de búsqueda/catálogo y endpoints opcionales explícitamente configurados.

## Observaciones del estado del repositorio

- La fábrica es `scrapers.api_stores.api_first_scrapers()` y se envuelve en `ApiStoresScraper`; `monitor_ofertas` también registra fuentes HTML/legacy en paralelo.
- `core.scraper_base.BaseScraper.get()` hace reintentos conservadores; `core.source_resilience` clasifica estados y circuitos.
- `VtexCatalogAdapter._queries()` y `discover()` derivan el prefijo de variables con sufijos diferentes; la integración debe armonizarlos sin cambiar sus endpoints configurables.
- `tiendas_mexico.py` ya usa Scrapling de forma opcional para Amazon, Soriana y Liverpool, y degrada limpiamente si no está instalado.
