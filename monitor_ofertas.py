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
            try:
                return set(json.load(f))
            except (json.JSONDecodeError, TypeError):
                return set()
    return set()


def guardar_vistos(vistos):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(vistos), f, ensure_ascii=False, indent=2)


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
    if not response.ok:
        raise RuntimeError(
            f"Telegram rechazó el mensaje: HTTP {response.status_code} - {response.text}"
        )


def revisar_tiendas(vistos):
    avisos = []

    for item in buscar_todas():
        descuento = int(item.get("descuento", 0))
        if not (MIN_DESCUENTO <= descuento <= MAX_DESCUENTO):
            continue

        clave = f"{item.get('tienda', '')}|{item.get('titulo', '')}"
        if clave in vistos:
            continue

        mensaje = (
            f"🔥 <b>{descuento}% DESCUENTO</b>\n\n"
            f"🏪 {item['tienda']}\n"
            f"{item['titulo']}\n\n"
            f"Antes: ${item['precio_anterior']:,} MXN\n"
            f"Ahora: ${item['precio_actual']:,} MXN\n\n"
            f"{item['url']}"
        )
        avisos.append((clave, mensaje))

    return avisos


def main():
    vistos = cargar_vistos()
    avisos = revisar_tiendas(vistos)
    enviados = 0

    for clave, aviso in avisos:
        enviar_telegram(aviso)
        vistos.add(clave)
        enviados += 1

    guardar_vistos(vistos)
    print(f"[{datetime.now()}] Revisión completa. {enviados} avisos enviados.")


if __name__ == "__main__":
    main()
