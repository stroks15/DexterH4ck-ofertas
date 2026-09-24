import requests
import json
import os
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MIN_DESCUENTO = 80  # %
MAX_DESCUENTO = 99  # %

# Palabras clave o categorías a monitorear en Mercado Libre.
# Agrega o quita las que quieras. Entre más genéricas, más resultados
# revisa (pero también tarda más y es más fácil toparte con "ofertas"
# falsas, como accesorios sueltos de un producto caro).
KEYWORDS_ML = [
    "iphone",
    "laptop",
    "television",
    "consola videojuegos",
    "tenis",
    "smartwatch",
    "bocina bluetooth",
]

CACHE_FILE = "vistos.json"


def cargar_vistos():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_vistos(vistos):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(vistos), f)


def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Falta configurar TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = requests.post(url, data=payload, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Error enviando a Telegram: HTTP {r.status_code} - {r.text}")
    print("Aviso enviado correctamente a Telegram.")


def calcular_descuento(precio_original, precio_actual):
    if not precio_original or precio_original <= precio_actual:
        return 0
    return round((1 - precio_actual / precio_original) * 100)


def revisar_mercado_libre(vistos):
    nuevos_avisos = []
    for kw in KEYWORDS_ML:
        url = "https://api.mercadolibre.com/sites/MLM/search"
        params = {"q": kw, "limit": 50}
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"Error consultando ML para '{kw}':", e)
            continue

        for item in data.get("results", []):
            item_id = item.get("id")
            precio_actual = item.get("price")
            precio_original = item.get("original_price")

            descuento = calcular_descuento(precio_original, precio_actual)

            if MIN_DESCUENTO <= descuento <= MAX_DESCUENTO and item_id not in vistos:
                titulo = item.get("title")
                link = item.get("permalink")
                mensaje = (
                    f"🔥 <b>{descuento}% de descuento</b> en Mercado Libre\n\n"
                    f"{titulo}\n"
                    f"Antes: ${precio_original:,.0f} MXN\n"
                    f"Ahora: ${precio_actual:,.0f} MXN\n\n"
                    f"{link}"
                )
                nuevos_avisos.append(mensaje)
                vistos.add(item_id)

    return nuevos_avisos


def main():
    vistos = cargar_vistos()
    avisos = revisar_mercado_libre(vistos)

    for aviso in avisos:
        enviar_telegram(aviso)

    guardar_vistos(vistos)
    print(f"[{datetime.now()}] Revisión completa. {len(avisos)} avisos enviados.")


if __name__ == "__main__":
    main()
