import os
import requests
from datetime import datetime
from scrapers.tiendas_mexico import buscar_todas
from database import inicializar, debe_alertar, guardar

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MIN_DESCUENTO = 60
MAX_DESCUENTO = 99


def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")

    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    response.raise_for_status()


def revisar_tiendas():
    avisos = []

    for item in buscar_todas():
        descuento = int(item.get("descuento", 0))

        if not (MIN_DESCUENTO <= descuento <= MAX_DESCUENTO):
            continue

        tienda = item.get("tienda", "Desconocida")
        titulo = item.get("titulo", "Sin titulo")
        url = item.get("url", "")
        precio = float(item.get("precio_actual", 0))

        clave = f"{tienda}|{titulo}"

        if not debe_alertar(clave, precio):
            continue

        mensaje = (
            f"🔥 <b>{descuento}% DESCUENTO</b>\n\n"
            f"🏪 <b>{tienda}</b>\n"
            f"{titulo}\n\n"
            f"💰 Precio liquidación: ${precio:,.2f} MXN\n"
            f"🔗 {url}"
        )

        avisos.append((clave, tienda, titulo, precio, url, mensaje))

    return avisos


def main():
    inicializar()
    avisos = revisar_tiendas()
    enviados = 0

    for clave, tienda, titulo, precio, url, mensaje in avisos:
        enviar_telegram(mensaje)
        guardar(clave, tienda, titulo, precio, url)
        enviados += 1

    print(f"[{datetime.now()}] Revisión completa. {enviados} avisos enviados.")


if __name__ == "__main__":
    main()
