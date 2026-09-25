# DexterH4ck-ofertas

Monitor de ofertas para tiendas de México con avisos por Telegram.

Fuentes: Walmart México, Bodega Aurrera, Chedraui, Mercado Libre México, Soriana, Liverpool y Amazon México.

Revisa búsquedas de tecnología, hogar, videojuegos, moda y electrónica cada 15 minutos.

Acepta descuentos del 60% al 99%. Primero intenta obtener precio actual y precio anterior publicados por la tienda. Si no existe precio anterior, conserva el mayor precio observado para detectar una bajada posterior.

Las liquidaciones se marcan cuando el producto o su tarjeta contiene términos como "liquidación", "remate", "outlet", "última pieza", "saldo", "caja abierta" u "open box".

No se usa la terminación .01/.02/.03 como prueba de liquidación, porque puede producir falsos positivos.

Configura TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en GitHub Actions.

El historial se guarda en historial_ofertas.json. Se eliminó la base SQLite del flujo porque era la causa de cambios sin preparar durante el rebase.
