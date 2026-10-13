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

- `WALMART_GRAPHQL_URL`, `WALMART_STORE_ID` (por defecto `2033`), `WALMART_TENANT` (por defecto `WM_MEXICO`), `WALMART_GRAPHQL_QUERY`
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

Estas terminaciones son **exclusivas de liquidaciones en sucursal (FÍSICAS)**. Solo suman **+30 puntos** cuando el candidato trae `tipo_fuente == "FISICA"`. En Walmart.com.mx y Bodegaaurrera.com.mx (web/APIs/proxy) las liquidaciones son rebajas matemáticas sobre el precio de lista, por lo que el detector online se basa únicamente en caídas reales de precio (50–99%). No implican stock garantizado en sucursal.

### Sanitización de precios

`core/price_sanitizer.py` impide que un tamaño/gramaje (`875g`, `1kg`, `400ml`) se interprete como `precio_anterior`: solo se aceptan nodos numéricos de precio (p. ej. `priceInfo.wasPrice.price`) o montos limpios (`$1,299.00`), y se descarta toda referencia que coincida con una medida declarada en el título. El historial contaminado se repara al cargarse.

### Proxy de Google Apps Script

`scrapers/google_proxy_scraper.py` consume por GET (`allow_redirects=True`, Apps Script responde 302) la Web App indicada en `APPS_SCRIPT_PROXY_URL` (Walmart, Bodega Aurrera, Mercado Libre, Chedraui y Soriana). La URL nunca se registra en logs.

### Política de descuentos

El monitor investiga **50–99%**. Los rangos de diagnóstico son **50–69%, 70–89%, 90–94% y 95–99%**. Toda liquidación **90–99%** con referencia válida se fuerza como alerta prioritaria y lleva el emoji **💣** al inicio. El 95/99% no es el único objetivo.

Una oferta de **95–99%** solo se envía si (1) nunca se envió antes —historial local y, si está configurado, Supabase—, (2) su enlace es válido (sin `null`/`undefined`/marcadores sin resolver) y (3) el mensaje lleva el 💣 al inicio.

### Historial en Supabase (opcional)

Para que "enviada una sola vez" sobreviva a rebases y ejecuciones efímeras de CI, configura los secretos `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` en GitHub Actions y ejecuta una vez el bloque `alertas_enviadas` de `supabase/liquidaciones_schema.sql` en tu proyecto (RLS activo, sin políticas: solo la clave de servicio accede). Sin esos secretos el monitor usa únicamente `historial_ofertas.json`; si Supabase falla en un ciclo, el historial local sigue bloqueando los reenvíos.


## Proxy Google Apps Script — versión corregida

El código fuente para pegar en el editor de Apps Script está en
`apps-script/DexterH4ckProxy.gs` (v2.2.0). La versión anterior construía URLs con
`{encodeURIComponent(termino)}` literal, que generaba URLs inválidas; ahora ese marcador
roto en las Propiedades del script se normaliza a `{q}` y el término se codifica una sola vez.

### Instalación

1. Abre el proyecto de Google Apps Script que ya despliegas como Web App.
2. Copia el contenido de `apps-script/DexterH4ckProxy.gs` al editor y guarda.
3. En **Configuración del proyecto → Propiedades del script**, configura opcionalmente:
   - `WALMART_JSON_SEARCH_URL`
   - `BODEGA_JSON_SEARCH_URL`
   - `SORIANA_JSON_SEARCH_URL`
4. Cada valor opcional debe ser una URL HTTPS de una fuente JSON autorizada que contenga el marcador `{q}`. No uses una página HTML de búsqueda como si fuera una API.
5. Actualiza la implementación de Web App para que use la nueva versión.
6. Prueba `.../exec?q=liquidacion` en el navegador. El JSON devuelve `offers`, `fuentes`, `errores` y `total_detected`.
7. Mantén la URL de implementación en el secreto de GitHub `APPS_SCRIPT_PROXY_URL`; nunca la pegues en el repositorio.

Mercado Libre y Chedraui se consultan mediante sus formatos de catálogo previstos en el script; su disponibilidad puede cambiar. Walmart, Bodega Aurrera y Soriana quedan como `not_configured` hasta que se proporcione una API JSON que funcione, y también cuando su endpoint responde con bloqueo/HTTP de error, una página que no es JSON o un JSON sin filas reconocibles; en ese caso `fuentes[].motivo` indica la causa real (`HTTP 403…`, `json_sin_filas_reconocibles`…) y las demás tiendas siguen normal. Esto evita informar falsamente que una tienda fue consultada con éxito. El script también descarta como contaminada cualquier oferta cuyo precio anterior coincida con un gramaje del título (`875g`, `1 kg`). La caché dura cuatro minutos y el parámetro `q` permite probar una búsqueda individual.

La integración Python reconoce la clave `offers` en inglés y conserva el estado de salud de las fuentes reportado por el proxy. Esta implementación no garantiza ofertas en cada ejecución: la disponibilidad real debe confirmarse en el JSON de la Web App.
