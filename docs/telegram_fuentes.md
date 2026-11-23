# Fuentes de Telegram

Adaptador: `scrapers/telegram_monitor_scraper.py` (dos workers aislados en `monitor_ofertas.py`).

| Fuente | Tipo | Lectura |
|---|---|---|
| Grupo Walmart Channel (prioritaria, `prioridad: 10`) | grupo privado | Telethon con cuenta de usuario ya unida |
| LiquidaZona Oficial, Ofertas Perronas MX, Ofertones México | canales públicos | vista web `https://t.me/s/<canal>` (sin credenciales) |

Se activan en `fuentes_liquidaciones.json` (`autolectura: true`, `estado: "activo"`).

## Configurar el grupo privado

Hay dos lectores independientes; no confundas sus credenciales:

### A. Lector Bot API (`scrapers/telegram_grupo_privado.py`)
- Usa `TELEGRAM_TOKEN` para consultar `getUpdates` y enviar las alertas al destino `TELEGRAM_CHAT_ID`.
- Agrega **el bot asociado a `TELEGRAM_TOKEN`** al grupo privado `Walmart Oferstock NACIONAL`. Para leer todos los mensajes, hazlo administrador o desactiva Privacy Mode en BotFather (`/setprivacy` → `Disable`).
- Opcional pero recomendado: configura `GRUPO_FUENTE_CHAT_ID` con el ID numérico del grupo. También admite `TELEGRAM_WALMART_CHAT_ID`. Si no se configura, intenta detectar el grupo por su título exacto a partir de actualizaciones nuevas.
- El enlace de invitación no sustituye agregar el bot ni revela por sí solo el ID. Si el bot no está dentro del grupo, no podrá leerlo.
- `getUpdates` requiere que no exista un webhook activo para ese mismo bot. El offset se guarda después de cada envío confirmado para reducir reenvíos si una ejecución falla.

### B. Lector Telethon (cuenta de usuario, opcional)
1. Crea `TELEGRAM_API_ID` y `TELEGRAM_API_HASH` en https://my.telegram.org.
2. En tu equipo (nunca en CI): `python tools/telegram_session_setup.py --unirse` y copia la cadena al secret `TELEGRAM_SESSION_STRING`. Usa una cuenta secundaria dedicada.
3. Opcional: `TELEGRAM_WALMART_INVITE` y/o `TELEGRAM_WALMART_CHAT_ID`.
4. Sin `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` y `TELEGRAM_SESSION_STRING`, solo se omite el lector Telethon; los canales públicos y el lector Bot API siguen su propio flujo.

**Importante:** agregar tu cuenta personal al grupo no agrega automáticamente al bot. El bot y la cuenta Telethon son identidades distintas.
## Reglas
- Solo URLs de Walmart, Bodega Aurrera, Chedraui, Soriana, Mercado Libre, Amazon, Coppel, Suburbia y Liverpool.
- Precios por `core/price_sanitizer.py`: "875g" o "1 kg" nunca son precio de referencia.
- Descuento 50-99 %, en cualquier tienda. Si el mensaje no trae precio anterior (lo normal en estos grupos), basta con que
  declare el porcentaje (`-70%`, `70% OFF`, `70% de descuento`, `descuento: 70%`). No cuentan "hasta 70%", cashback, cupones,
  bonificaciones ni meses sin intereses. Con precio anterior manda el cálculo real. Debajo de 50 % o por encima de 99 % no sale.
  Si el descuento viene solo del mensaje, la alerta lo indica y no muestra un "antes" inventado.
- De 95 a 99 %: una sola vez (historial local + Supabase), enlace válido, y 💣 al inicio del mensaje.
- Una sesión de Telethon no debe usarse en dos sitios a la vez: el workflow ya serializa ciclos (`concurrency`).
