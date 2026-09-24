import os
import json
import requests
from datetime import datetime
from scrapers.tiendas_mexico import buscar_todas

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MIN_DESCUENTO = 60
MAX_DESCUENTO = 99
CACHE_FILE = "vistos.json"


def cargar_vistos():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_vistos(vistos):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(vistos), f)


def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Faltan secretos de Telegram")
        return

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto,
            "parse_mode": "HTML"
        },
        timeout=15
    )


def revisar_tiendas(vistos):
    avisos = []

    for item in buscar_todas():
        clave = item["tienda"] + item["titulo"]

        if item["descuento"] >= MIN_DESCUENTO and clave not in vistos:
            mensaje = (
                f"🔥 <b>{item['descuento']}% DESCUENTO</b>\n\n"
                f"🏪 {item['tienda']}\n"
                f"{item['titulo']}\n\n"
                f"Antes: ${item['precio_anterior']:,} MXN\n"
                f"Ahora: ${item['precio_actual']:,} MXN\n\n"
                f"{item['url']}"
            )
            avisos.append(mensaje)
            vistos.add(clave)

    return avisos


def main():
    vistos = cargar_vistos()
    avisos = revisar_tiendas(vistos)

    for aviso in avisos:
        enviar_telegram(aviso)

    guardar_vistos(vistos)
    print(datetime.now(), len(avisos), "ofertas enviadas")


if __name__ == "__main__":
    main()
