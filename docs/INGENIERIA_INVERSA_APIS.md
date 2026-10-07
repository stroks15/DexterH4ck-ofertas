# Ingeniería inversa de APIs y peticiones web

## Arquitectura aplicada

El monitor usa esta prioridad:

1. API pública/documentada.
2. Endpoint JSON configurado explícitamente y permitido para nuestro uso.
3. JSON capturado de fetch/XHR mediante navegador, sólo cuando se habilita.
4. JSON embebido como JSON-LD o __NEXT_DATA__.
5. HTML estructurado.
6. Índices públicos/fuentes comunitarias como respaldo.

La finalidad es que un cambio de CSS no rompa el descubrimiento si la tienda
sigue entregando los datos estructurados que necesita el navegador.

## Cómo investigar una tienda

En Chrome o Firefox:

1. Abrir una búsqueda real.
2. DevTools -> Network/Red.
3. Filtrar Fetch/XHR.
4. Buscar un producto.
5. Localizar la respuesta JSON que contiene nombre, precio, referencia,
   identificador y URL.
6. Copy -> Copy as cURL.
7. Revisar método, URL, parámetros y headers.
8. No guardar cookies, tokens personales ni credenciales en GitHub.
9. Crear un fixture JSON con la respuesta.
10. Probar el parser antes de conectar el endpoint en producción.

## Contrato normalizado

Los adaptadores deben producir tienda, título, precio actual, precio anterior,
descuento, URL directa, identificador estable cuando exista, marca, categoría
y origen.

El descuento siempre se calcula localmente. Una referencia menor o igual al
precio actual se descarta y nunca se inventa un precio anterior.

## Captura opcional con Playwright

scrapers/browser_network.py observa respuestas JSON de recursos fetch/XHR de
una página. No usa stealth, resolución de CAPTCHA, rotación de proxies ni
técnicas para eludir WAF.

Se habilita con:

NETWORK_BROWSER_ENABLED=true

Y se configura NETWORK_BROWSER_SOURCES como un JSON, por ejemplo:

[{"store":"Soriana","url":"https://www.soriana.com/buscar?q=ofertas"}]

El extractor core/network_json.py reconoce variantes comunes de precios,
referencias, nombres, URLs e IDs dentro de JSON anidado.

## Integración por tienda

Chedraui ya dispone de un adaptador VTEX.

Walmart y Bodega tienen adaptadores GraphQL configurables; no se inventa un
endpoint ni un esquema.

Coppel aprovecha __NEXT_DATA__ cuando la página pública lo entrega.

Mercado Libre usa la API oficial cuando está disponible y mantiene fallback
cuando recibe 401/403/429.

Soriana, Suburbia, Liverpool, Oferstock y Amazon pueden recibir un endpoint
JSON real mediante sus variables de entorno, o pasar por la captura de red
opt-in mientras identificamos la petición estable.

## Regla de validación

HTTP 200 no significa que una fuente funcione. Una fuente sólo se considera
operativa si produce candidatos con precio, URL de producto y datos coherentes.

Toda nueva integración debe incluir al menos un fixture JSON y una prueba
determinista.
