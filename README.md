# DexterH4ck-ofertas

Monitor de ofertas y liquidaciones para tiendas de México con avisos por Telegram.

## Fuentes comerciales
- Walmart México
- Bodega Aurrera
- Chedraui
- Mercado Libre México
- Soriana
- Liverpool
- Amazon México

Revisa búsquedas de tecnología, hogar, videojuegos, moda, electrónica y nuevas categorías de alta demanda cada 15 minutos.

## Motor de liquidaciones

- \`MIN_DESCUENTO = 50\`
- \`MAX_DESCUENTO = 99\`
- Mantiene historial de precios para detectar caídas aunque la tienda no publique precio anterior.
- La puntuación combina:
  - descuento: hasta 50 puntos
  - marca prioritaria: +20
  - categoría de alta demanda: +15
  - palabra \`liquidación\`: +10
  - última pieza/outlet: +10
- Las alertas que cumplen el mínimo se ordenan por puntuación antes de enviarse a Telegram.

## Categorías

Celulares, Laptops, Tablets, TV, Consolas, Videojuegos, Audio, Línea blanca, Lavadoras, Refrigeradores, Microondas, Colchones, Muebles, Ropa, Calzado, Bebés, Pañales, Cunas, Carriolas, Juguetes, Belleza, Maquillaje, Mascotas, Herramientas y Hogar.

Las marcas prioritarias están en \`marcas_prioritarias.json\`.

## Chedraui

El normalizador reconoce fichas de producto de Chedraui con rutas tipo \`/.../p\` y conserva nombre, marca, categoría, precio, descuento y URL cuando están presentes en los datos estructurados o en la tarjeta del producto.

## Fuentes externas

\`fuentes_liquidaciones.json\` conserva los canales de Telegram y Oferstock como fuentes preparadas para futuras integraciones. Un enlace por sí solo no concede acceso automático a mensajes de canales/grupos; por eso permanecen desactivadas hasta implementar el adaptador correspondiente.

## Telegram

Configura \`TELEGRAM_TOKEN\` y \`TELEGRAM_CHAT_ID\` en GitHub Actions.

El historial se guarda en \`historial_ofertas.json\`. Se mantiene el flujo sin SQLite para evitar cambios sin preparar durante el rebase de GitHub Actions.


## Respaldo de IA

El monitor usa una cadena de respaldo para evitar que un error temporal de Gemini detenga el procesamiento:

1. **Gemini** como proveedor principal.
2. **Groq** como respaldo automático cuando Gemini devuelve 429, 5xx, timeout o no entrega JSON válido.
3. **Sin IA** si ambos proveedores no están disponibles.

Configura en **GitHub → Settings → Secrets and variables → Actions**:
- `GEMINI_API_KEY`
- `GROQ_API_KEY`

Opcionales:
- `GEMINI_MODEL`
- `GROQ_MODEL` (por defecto `openai/gpt-oss-20b`)

Las claves nunca deben guardarse dentro del repositorio.

## Chedraui

Chedraui utiliza una estructura de URLs distinta a la ruta genérica `/search?q=...`. Para búsquedas de jardinería, el monitor usa como respaldo la categoría pública vigente de Patio y jardín cuando la búsqueda genérica devuelve HTTP 404. La ficha de producto sigue siendo validada antes de publicarse.


## Arquitectura multiscraper API-first (2026-10)

El monitor mantiene dos capas de descubrimiento:

1. **API-first** en `scrapers/api_stores.py`: Chedraui usa su catálogo VTEX y Mercado Libre usa su API pública. Walmart/Bodega y los demás adaptadores admiten endpoints JSON/GraphQL autorizados mediante variables de entorno.
2. **Legacy paralelo** en `scrapers/tiendas_mexico.py`: cada tienda se ejecuta en un worker aislado para que un timeout o 429 no detenga a las demás.

La interfaz común está en `core/scraper_base.py`. Los requests usan timeouts, backoff y límites conservadores; el proyecto **no intenta saltar CAPTCHA/WAF ni falsificar la identidad de aplicaciones móviles**.

### Variables opcionales API-first

- `WALMART_GRAPHQL_URL`, `WALMART_STORE_ID`, `WALMART_GRAPHQL_QUERY`
- `BODEGA_GRAPHQL_URL`, `BODEGA_STORE_ID`, `BODEGA_GRAPHQL_QUERY`
- `CHEDRAUI_VTEX_ENDPOINT`
- `SORIANA_API_ENDPOINT`
- `COPPEL_API_ENDPOINT`
- `SUBURBIA_API_ENDPOINT`
- `AMAZON_API_ENDPOINT`
- `ML_QUERIES`

Los endpoints de terceros solo deben configurarse cuando estén documentados o autorizados para la integración. El historial local/Supabase sigue siendo la referencia para detectar bajadas reales cuando una API solo entrega el precio actual.

### Señales de liquidación física

- Walmart/Bodega: `.03`, `.02`, `.01`
- Soriana: `.02`, `.05`

Estas señales agregan **+30 puntos** y pueden generar alerta aunque no exista precio anterior online. No implican stock garantizado en sucursal.

### Política de descuentos

El monitor investiga **50–99%**. Los rangos de diagnóstico son **50–69%, 70–89%, 90–94% y 95–99%**. Toda liquidación **90–99%** con referencia válida se fuerza como alerta prioritaria y lleva el emoji **💣** al inicio. El 95/99% no es el único objetivo.
