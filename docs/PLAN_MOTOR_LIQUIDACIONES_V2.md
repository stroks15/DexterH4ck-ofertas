# Motor de liquidaciones V2

Objetivo: convertir DexterH4ck-ofertas en un cazador de ofertas con historial real de precios.

## Tiendas integradas

- Walmart México
- Bodega Aurrera
- Soriana
- Chedraui
- Coppel
- Liverpool
- Mercado Libre México
- Amazon México

## Flujo

1. Scrapers obtienen productos.
2. Normalizador unifica nombre, precio, tienda y URL.
3. Motor de liquidación calcula:
   - descuento real
   - caída contra historial
   - palabras clave
   - disponibilidad
4. Supabase guarda historial.
5. Telegram recibe únicamente oportunidades nuevas.

## Reglas

No usar terminaciones .01/.02/.03 como única prueba.

Priorizar:
- descuento >= 60%
- caída histórica importante
- palabras de liquidación
- producto repetible y verificable

## Próximos módulos

- supabase_client.py
- price_history.py
- liquidation_engine.py
- telegram_formatter.py
