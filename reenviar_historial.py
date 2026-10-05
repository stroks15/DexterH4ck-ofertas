import json
import os
import time
import requests

HISTORIAL_FILE = "historial_ofertas.json"
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def enviar(texto):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
    for intento in range(2):
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        if response.ok:
            return
        if response.status_code == 429 and intento == 0:
            try:
                espera = int(response.json().get("parameters", {}).get("retry_after", "5"))
            except (ValueError, TypeError):
                espera = 5
            time.sleep(min(max(espera, 1), 120) + 0.5)
            continue
        raise RuntimeError(f"Telegram HTTP {response.status_code}: {response.text[:300]}")


with open(HISTORIAL_FILE, "r", encoding="utf-8") as archivo:
    historial = json.load(archivo)

contador = 0
for _, oferta in historial.items():
    if not isinstance(oferta, dict) or "titulo" not in oferta:
        continue

    mensaje = (
        "📦 <b>Historial de oferta</b>\n\n"
        f"🏪 {oferta.get('tienda', 'Desconocida')}\n"
        f"🛒 {oferta.get('titulo')}\n"
        f"💰 Precio: ${oferta.get('precio_actual', 0):,.2f} MXN\n"
        f"🔗 {oferta.get('url', '')}"
    )

    enviar(mensaje)
    contador += 1
    time.sleep(1)

print(f"Ofertas reenviadas: {contador}")
