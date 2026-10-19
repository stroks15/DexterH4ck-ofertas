# Fuentes de Telegram

Adaptador: `scrapers/telegram_monitor_scraper.py` (dos workers aislados en `monitor_ofertas.py`).

| Fuente | Tipo | Lectura |
|---|---|---|
| Grupo Walmart Channel (prioritaria, `prioridad: 10`) | grupo privado | Telethon con cuenta de usuario ya unida |
| LiquidaZona Oficial, Ofertas Perronas MX, Ofertones México | canales públicos | vista web `https://t.me/s/<canal>` (sin credenciales) |

Se activan en `fuentes_liquidaciones.json` (`autolectura: true`, `estado: "activo"`).

## Configurar el grupo privado
1. `TELEGRAM_API_ID` y `TELEGRAM_API_HASH` se crean en https://my.telegram.org (ya agregados como secrets).
2. En tu equipo (nunca en CI): `python tools/telegram_session_setup.py --unirse` y copia la cadena al
   secret `TELEGRAM_SESSION_STRING`. Usa una cuenta secundaria dedicada.
3. Secret `TELEGRAM_WALMART_INVITE` con el enlace de invitación. **No lo pongas en el JSON: el repositorio es público** (una prueba falla si aparece `t.me/+`).
   Alternativa: `TELEGRAM_WALMART_CHAT_ID`, o el título del grupo (`titulo_hint`: "walmart").
4. Sin esas variables el grupo privado se omite (`estado=not_configured`) y los canales públicos siguen.

## Reglas
- Solo URLs de Walmart, Bodega Aurrera, Chedraui, Soriana, Mercado Libre, Amazon, Coppel, Suburbia y Liverpool.
- Precios por `core/price_sanitizer.py`: "875g" o "1 kg" nunca son precio de referencia.
- Descuento 50-99 %, en cualquier tienda. Si el mensaje no trae precio anterior (lo normal en estos grupos), basta con que
  declare el porcentaje (`-70%`, `70% OFF`, `70% de descuento`, `descuento: 70%`). No cuentan "hasta 70%", cashback, cupones,
  bonificaciones ni meses sin intereses. Con precio anterior manda el cálculo real. Debajo de 50 % o por encima de 99 % no sale.
  Si el descuento viene solo del mensaje, la alerta lo indica y no muestra un "antes" inventado.
- De 95 a 99 %: una sola vez (historial local + Supabase), enlace válido, y 💣 al inicio del mensaje.
- Una sesión de Telethon no debe usarse en dos sitios a la vez: el workflow ya serializa ciclos (`concurrency`).
