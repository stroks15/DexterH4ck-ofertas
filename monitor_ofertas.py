import html
import json
import os
from datetime import datetime, timezone
import requests
from scrapers.tiendas_mexico import buscar_todas

MIN_DESCUENTO = 60
MAX_DESCUENTO = 99
HISTORIAL_FILE = "historial_ofertas.json"
MAX_HISTORIAL = 10000
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def cargar_historial():
    if not os.path.exists(HISTORIAL_FILE):
        return {}
    try:
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as archivo:
            data = json.load(archivo)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}

def guardar_historial(historial):
    if len(historial) > MAX_HISTORIAL:
        historial = dict(sorted(historial.items(), key=lambda par: par[1].get("ultima_actualizacion", ""), reverse=True)[:MAX_HISTORIAL])
    temporal = f"{HISTORIAL_FILE}.tmp"
    with open(temporal, "w", encoding="utf-8") as archivo:
        json.dump(historial, archivo, ensure_ascii=False, indent=2)
    os.replace(temporal, HISTORIAL_FILE)

def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Telegram rechazó el mensaje: HTTP {response.status_code} - {response.text}")

def calcular_datos(item, anterior_hist):
    actual = float(item.get("precio_actual") or 0)
    listado = float(item["precio_anterior"]) if item.get("precio_anterior") else None
    referencia = max([x for x in (listado, anterior_hist.get("precio_maximo")) if x], default=0)
    dcto = round((1 - actual / referencia) * 100) if referencia > actual else 0
    return actual, referencia, dcto

def revisar():
    historial = cargar_historial()
    avisos = []
    vistos_en_esta_revision = set()

    for item in buscar_todas():
        titulo = str(item.get("titulo", "")).strip()
        url = str(item.get("url", "")).strip()
        tienda = str(item.get("tienda", "Desconocida")).strip()
        if not titulo or not url:
            continue

        clave = item.get("id") or f"{tienda}|{titulo}|{url}"
        if clave in vistos_en_esta_revision:
            continue
        vistos_en_esta_revision.add(clave)

        anterior_hist = historial.get(clave, {})
        actual, referencia, dcto = calcular_datos(item, anterior_hist)
        if actual <= 0:
            continue

        historial[clave] = {
            "tienda": tienda,
            "titulo": titulo,
            "url": url,
            "precio_actual": actual,
            "precio_maximo": max(actual, referencia),
            "ultima_actualizacion": datetime.now(timezone.utc).isoformat(),
            "precio_alertado": anterior_hist.get("precio_alertado"),
        }

        if not (MIN_DESCUENTO <= dcto <= MAX_DESCUENTO):
            continue

        ultimo_alertado = anterior_hist.get("precio_alertado")
        if ultimo_alertado is not None and actual >= float(ultimo_alertado):
            continue

        etiqueta = "🔥 LIQUIDACIÓN" if item.get("liquidacion") else "🚨 OFERTA"
        mensaje = (
            f"{etiqueta} <b>{dcto}% DE DESCUENTO</b>\n\n"
            f"🏪 <b>{html.escape(tienda)}</b>\n"
            f"🛒 {html.escape(titulo)}\n\n"
            f"💰 Ahora: <b>\${actual:,.2f} MXN</b>\n"
            f"💵 Antes/referencia: \${referencia:,.2f} MXN\n"
            f"🔗 {html.escape(url)}"
        )
        avisos.append((clave, actual, mensaje))

    enviados = 0
    for clave, actual, mensaje in avisos:
        enviar_telegram(mensaje)
        historial[clave]["precio_alertado"] = actual
        enviados += 1

    guardar_historial(historial)
    print(f"[{datetime.now().isoformat()}] Candidatos: {len(vistos_en_esta_revision)} | Avisos enviados: {enviados}")

if __name__ == "__main__":
    revisar()
