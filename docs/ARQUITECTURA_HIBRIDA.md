# Arquitectura híbrida de descubrimiento

El monitor combina cuatro capas independientes y tolerantes a fallos:

1. **APIs verificadas/configuradas**: los adaptadores de `scrapers/api_stores.py` y los adaptadores GraphQL/VTEX existentes. Un endpoint no configurado, bloqueado o con respuesta no JSON se reporta sin detener las demás tiendas.
2. **Datos estructurados**: `scrapers/hybrid_discovery.py` procesa únicamente JSON-LD (`Product`/`Offer`) y metadatos Open Graph con URL y precio explícitos. No extrae números de texto libre.
3. **Playwright**: captura respuestas JSON públicas (`xhr`/`fetch`) y el HTML renderizado de URLs declaradas en `HYBRID_PLAYWRIGHT_SOURCES`. No usa stealth, CAPTCHA solving, rotación de proxies ni evasión de WAF.
4. **Scrapling**: `scrapers/tiendas_mexico.py` funciona como fallback HTML con robots/autothrottle, límites por dominio y cooldown persistente ante 403/412/429/503.

La prioridad es API-first. Scrapling se ejecuta después para tiendas sin productos obtenidos por API; la capa híbrida estructurada/Playwright es una fuente adicional opt-in. Todas las fuentes están aisladas por hilo y sus estados se guardan en `monitor_health.json`.

## Configuración

Las variables son opcionales y no deben contener secretos:

```text
HYBRID_DISCOVERY_ENABLED=true
HYBRID_STRUCTURED_SOURCES=[{"store":"Tienda","url":"https://dominio.example/catalogo-publico"}]
HYBRID_PLAYWRIGHT_SOURCES=[{"store":"Tienda","url":"https://dominio.example/busqueda-publica"}]
HYBRID_PLAYWRIGHT_WAIT_MS=2500
```

Solo deben registrarse URLs públicas comprobadas y permitidas por la tienda. No se deben inventar endpoints internos ni almacenar cookies/tokens en variables versionadas. Si una fuente devuelve 401/403/429/503, el monitor registra el estado, respeta `Retry-After` cuando existe y continúa con las otras.

## Evidencia y normalización

Los candidatos procedentes de JSON-LD/XHR conservan `origen_link=network_json`, URL canónica, identificador cuando existe y precios explícitos. El motor común valida la referencia, rechaza medidas como precios y calcula descuentos solo cuando el precio anterior es mayor que el actual. La deduplicación posterior del monitor unifica los candidatos de todas las capas.

## Verificación

La suite se ejecuta con:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

La prueba de la capa estructurada usa un fixture JSON-LD local; las consultas reales se realizan solo durante el workflow cuando el operador configura URLs públicas verificadas.
