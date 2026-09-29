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
