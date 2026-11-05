# Arquitectura de liquidaciones: Scrapling + Firecrawl

```text
monitor_ofertas.py
├── scrapers/api_stores.py                 # API-first: Mercado Libre, VTEX y JSON/GraphQL
├── scrapers/tiendas_mexico.py             # Scrapling Spider legacy, fallback multi-session
├── scrapers/browser_network.py            # Playwright normal: render JS + captura XHR/JSON
├── scrapers/firecrawl_enrichment.py       # Firecrawl v2: Markdown para contexto RAG
├── core/price_sanitizer.py                # Parser monetario, gramajes y reparación de historial
├── core/liquidation_engine.py             # Score y señales FISICA/ONLINE
├── core/supabase_history.py               # Dedupe persistente Diamante (opcional)
├── tools/telegram_bot.py                  # MarkdownV2, rangos y enlaces limpios
├── tools/firecrawl_rag_export.py          # Exportador JSONL opt-in
├── supabase/migrations/                   # Tabla de idempotencia para Supabase
├── historial_ofertas.json                # Fallback de historial versionado
├── fuentes_cooldown.json                  # Salud/cooldown por tienda
└── .github/workflows/monitor.yml          # Cron */15, pruebas y persistencia
```

## Extracción API-first + fallback Scrapling

1. `scrapers/api_stores.py` ejecuta primero los adaptadores oficiales/configurados: API pública de Mercado Libre, catálogos VTEX y endpoints JSON/GraphQL autorizados por variables de entorno.
2. `monitor_ofertas.py` identifica las tiendas que no devolvieron productos y sólo para ellas ejecuta el Spider de `scrapers/tiendas_mexico.py`.
3. El Spider usa dos sesiones HTTP normales de Scrapling (`http` y `catalog`), `sid` explícito, concurrencia por dominio 1, `robots.txt` y AutoThrottle.
4. La respuesta 403/412/429/503 termina el trabajo de esa tienda en el ciclo, escribe el motivo exacto y `Retry-After` cuando está presente en `fuentes_cooldown.json`, y permite continuar con las demás.

## Orden de ejecución API-first

El orquestador ejecuta primero los adaptadores API/JSON y las fuentes comunitarias. Solo cuando una tienda legacy (Amazon, Soriana o Liverpool) no devolvió productos por API se habilita su búsqueda Scrapling. Así se evita duplicar tráfico y la extracción HTML nunca compite con una API que ya respondió. El resto de fuentes mantiene aislamiento por worker, timeout y reporte de salud.

## Mapa de directorios

```text
.github/workflows/   monitor, CI de regresión y tareas de descubrimiento/reenvío
apps-script/         proxy JSON de Apps Script ya existente
config/              endpoints, fuentes Telegram, tiendas, marcas y categorías
core/                sanitización, identidad, score, red, Supabase y contratos base
docs/                arquitectura, planes y decisiones técnicas
scrapers/            API-first, VTEX, HTML Scrapling, browser XHR y comunidades
scripts/             instalación de entorno de desarrollo Firecrawl
supabase/            SQL de historial de liquidaciones
tests/               pruebas de regresión y contratos de integración
tools/               bot Telegram, exportador RAG y diagnósticos
monitor_ofertas.py   punto de entrada y orquestador
reenviar_historial.py reenvío manual, no programado automáticamente
```

## Límites de seguridad de red

La petición de combinar `FetcherSession(impersonate='chrome')` y `AsyncStealthySession` con la prohibición de falsificar fingerprints/evadir WAF es contradictoria: la impersonación TLS y las sesiones stealth son funciones anti-bot. Por eso esta implementación fija `impersonate=None`, `stealthy_headers=False` y `retries=0`; la captura JS existente usa Playwright normal. No resuelve CAPTCHA, no muta identidades de apps, no rota proxies y no cambia a un proxy/renderizador para sortear un 403. El Spider deja que las respuestas HTTP lleguen al callback de clasificación y mantiene `max_blocked_retries=0`.

## Sanitización, score y Telegram

- `core/price_sanitizer.py` acepta sólo montos completos (`$1,299.00` → `1299.0`), rechaza valores que contienen unidades/gramajes y repara en memoria campos históricos contaminados.
- El score suma hasta 50 puntos por descuento matemático, +20 marca prioritaria, +15 categoría de alta demanda, +10 keyword liquidación y +10 outlet/última pieza. Se limita a 100 puntos.
- La terminación `.01/.02/.03` de Walmart/Bodega y `.02/.05` de Soriana suma +30 sólo si `tipo_fuente == FISICA`. Una captura `ONLINE` no recibe ese bono.
- `tools/telegram_bot.py` genera MarkdownV2: Bronce 🏷️ (50–69), Plata 🔥 (70–89), Oro ⚡ (90–94) y Diamante 💣 (95–99). Si no hay precio anterior matemático, el mensaje identifica el porcentaje como reportado por la fuente en lugar de presentarlo como cálculo real.
- Las alertas Diamante requieren URL limpia y ausencia de la llave canónica tanto en Supabase como en el historial JSON. Supabase no disponible significa fallback local; nunca SQLite.

## Firecrawl y GitHub Actions

Firecrawl sólo exporta Markdown de URLs explícitamente enumeradas en `FIRECRAWL_RAG_URLS`; ese contenido sirve como contexto RAG, nunca como precio oficial. El adaptador usa `POST /v2/scrape`, carga `FIRECRAWL_API_KEY` desde el entorno y falla de forma no bloqueante. Sin API key, la etapa se omite. No se ejecuta un login de navegador en CI.

El workflow corre cada 15 minutos (`*/15 * * * *`), instala Scrapling y Playwright normal, valida Python y ejecuta toda la suite de pruebas. Persiste `historial_ofertas.json`, `fuentes_cooldown.json` y offsets de Telegram mediante commit de Git. Para la deduplicación Diamante se requiere aplicar `supabase/migrations/202611050001_alertas_enviadas.sql` y configurar `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`; sin ellos se usa el JSON local.

## Instalación de skills Firecrawl (estación de desarrollo)

```bash
bash scripts/setup_firecrawl.sh
```

El script instala la skill de integración, inicializa Firecrawl CLI y prepara las skills de build. La autenticación con navegador es una configuración única en una estación de desarrollo; GitHub Actions no ejecuta ese login. El flag `--browser` pertenece al comando `init`, no a `setup build`.
