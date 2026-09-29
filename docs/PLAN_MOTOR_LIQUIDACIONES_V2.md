# Motor de liquidaciones V3

## Objetivo

Detectar oportunidades desde 50% hasta 99%, conservar contexto de producto y priorizar las alertas más relevantes.

## Tiendas

- Walmart México
- Bodega Aurrera
- Soriana
- Chedraui
- Coppel
- Liverpool
- Mercado Libre México
- Amazon México

## Puntuación

- Descuento: hasta 50 puntos.
- Marca prioritaria: +20.
- Categoría de alta demanda: +15.
- Palabra \`liquidación\`: +10.
- Última pieza/outlet: +10.
- Máximo: 100.

La puntuación sirve para ordenar las alertas; el umbral de descuento real sigue siendo 50% salvo la ruta de liquidación sin referencia histórica.

## Normalización

Cada candidato intenta conservar:
- nombre
- marca
- categoría
- precio actual
- precio anterior/referencia
- descuento
- URL
- indicador de liquidación/outlet

También se normalizan catálogos con campos equivalentes a \`nombre\`, \`precio\`, \`precio_original\`, \`descuento_pct\`, \`categoria\`, \`marca\`, \`stock\` y \`outlet\`, como los encontrados en el ZIP analizado.

## Chedraui

Se reconocen URLs de ficha que terminan en \`/p\` y otras variantes de producto. Se evita tratarlas como URLs de búsqueda.

## Fuentes externas

Los canales de Telegram y Oferstock quedan registrados en \`fuentes_liquidaciones.json\` para una futura integración. No se intenta leerlos automáticamente únicamente a partir del enlace.

## Deduplicación

Se conserva el historial por producto y se prioriza la URL del producto. El motor ordena los avisos por puntuación antes del envío.
