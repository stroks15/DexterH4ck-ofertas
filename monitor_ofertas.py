import html
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from core.liquidation_engine import evaluate_product
from scrapers.tiendas_mexico import buscar_todas
from scrapers.telegram_ofertas import buscar_telegram
from scrapers.tiendas_fisicas import buscar_tiendas_fisicas

MIN_DESCUENTO = 50
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

def formato_alerta_tipo(tipo):
    return "🟢" if tipo == "VERDE" else "🔴"

def es_enlace_producto_directo(url):
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not host or host in ("t.me", "telegram.me", "www.google.com", "google.com"):
        return False
    if any(x in path for x in ("/search", "/buscar", "/ofertas", "/oferta", "/catalogo", "/marcas", "/home", "/social/")):
        return False
    patrones_por_tienda = {
        "walmart.com.mx": ("/ip/",),
        "bodegaaurrera.com.mx": ("/ip/",),
        "chedraui.com.mx": ("/p",),
        "coppel.com": ("/pdp/",),
        "amazon.com.mx": ("/dp/", "/gp/product/"),
        "mercadolibre.com.mx": ("/mlm-", "/p/"),
        "liverpool.com.mx": ("/tienda/pdp/", "/pdp/"),
        "soriana.com": ("/producto/", "/p/"),
        "suburbia.com.mx": ("/p/", "/producto/"),
    }
    for dominio, patrones in patrones_por_tienda.items():
        if host == dominio or host.endswith("." + dominio):
            return any(p in path for p in patrones)
    return len(path.strip("/")) > 12

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
    actual = float(item.get("precio_actual") or item.get("price") or 0)
    listado = float(item["precio_anterior"]) if item.get("precio_anterior") else None
    referencia = max([x for x in (listado, anterior_hist.get("precio_maximo")) if x], default=0)
    dcto = round((1 - actual / referencia) * 100) if referencia > actual else 0
    return actual, referencia, dcto

def revisar():
    historial = cargar_historial()
    avisos = []
    vistos_en_esta_revision = set()

    meta = historial.get("__meta__", {})
    rearmar_alertas = not bool(meta.get("rearmado_alertas_2026_09_25"))
    if rearmar_alertas:
        meta["rearmado_alertas_2026_09_25"] = True
        historial["__meta__"] = meta
        print("Rearmado único de alertas activado: se volverán a enviar las ofertas vigentes.")

    candidatos = buscar_todas()
    candidatos.extend(buscar_telegram(requests.Session()))
    candidatos.extend(buscar_tiendas_fisicas(requests.Session()))
    print(f"Fuentes adicionales Telegram + físicas: {len(candidatos)} candidatos totales")
    for item in candidatos:
        titulo = str(item.get("titulo") or item.get("title") or item.get("nombre") or "").strip()
        url = str(item.get("url") or "").strip()
        tienda = str(item.get("tienda") or item.get("store") or "Desconocida").strip()
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

        enriched = dict(item)
        enriched["precio_actual"] = actual
        enriched["precio_anterior"] = referencia or item.get("precio_anterior")
        scoring = evaluate_product({**enriched, "precio_anterior": referencia})
        scoring["descuento"] = dcto
        extreme = scoring.get("extremo", {})

        registro = historial.get(clave)
        if not registro or float(registro.get("precio_actual", 0)) != actual:
            historial[clave] = {
                "tienda": tienda,
                "titulo": titulo,
                "marca": scoring.get("marca") or item.get("marca", ""),
                "categoria": scoring.get("categoria") or item.get("categoria", ""),
                "url": url,
                "precio_actual": actual,
                "precio_maximo": max(actual, referencia),
                "descuento": dcto,
                "puntuacion": scoring["puntuacion"],
                "extremo": extreme,
                "ultima_actualizacion": datetime.now(timezone.utc).isoformat(),
                "precio_alertado": anterior_hist.get("precio_alertado"),
            }
        else:
            historial[clave]["precio_maximo"] = max(float(historial[clave].get("precio_maximo", 0)), actual, referencia)
            historial[clave]["descuento"] = dcto
            historial[clave]["puntuacion"] = scoring["puntuacion"]
            historial[clave]["extremo"] = extreme
            if scoring.get("marca"):
                historial[clave]["marca"] = scoring["marca"]
            if scoring.get("categoria"):
                historial[clave]["categoria"] = scoring["categoria"]

        ultimo_alertado = anterior_hist.get("precio_alertado")
        if not rearmar_alertas and ultimo_alertado is not None and actual >= float(ultimo_alertado):
            continue

        # VERDE = descuento comprobable; ROJA = liquidación/ocasión sin referencia.
        es_descuento_real = MIN_DESCUENTO <= dcto <= MAX_DESCUENTO
        tipo_fuente = str(item.get("tipo_fuente") or "").upper()
        es_fisica = tipo_fuente == "FISICA"
        es_enlace = es_enlace_producto_directo(url)
        if es_fisica:
            host_evidencia = urlparse(url).netloc.lower()
            es_enlace = bool(host_evidencia) and host_evidencia not in ("www.google.com", "google.com", "t.me", "telegram.me")
        tiene_precio = actual > 0
        tiene_referencia = referencia > actual
        if not tiene_precio or not es_enlace:
            continue

        marca = scoring.get("marca") or item.get("marca")
        categoria = scoring.get("categoria") or item.get("categoria") or "Otros / Miscelánea"
        puntuacion = scoring.get("puntuacion", 0)

        if es_descuento_real and tiene_referencia:
            tipo_alerta = "VERDE"
            etiqueta = "🟢🚨 OFERTA"
            bloque_descuento = f"{dcto}% DE DESCUENTO\n"
            referencia_texto = f"💵 Antes/referencia: ${referencia:,.2f} MXN\n"
            ahorro = max(referencia - actual, 0)
            ahorro_texto = f"🤓💲 Ahorrado: ${ahorro:,.2f} MXN\n"
        else:
            tipo_alerta = "ROJA"
            etiqueta = "🔴🔥 LIQUIDACIÓN / OFERTA ESPECIAL"
            bloque_descuento = "DESCUENTO NO COMPARABLE\n"
            referencia_texto = ""
            ahorro = 0
            ahorro_texto = ""
        extras = []
        if scoring.get("marca_prioritaria"):
            extras.append("⭐ marca prioritaria")
        if scoring.get("categoria_alta_demanda"):
            extras.append("📈 categoría alta demanda")
        if "liquidacion" in scoring.get("indicadores", []):
            extras.append("🔥 palabra liquidación")
        if "ultima_pieza_outlet" in scoring.get("indicadores", []):
            extras.append("🏷️ última pieza/outlet")
        if precio_extremo:
            extras.append(f"💥 {nivel_extremo.lower()}")
        if precio_extremo_verificado:
            extras.append("✅ precio comprobado en página oficial")
        elif precio_extremo:
            extras.append("⚠️ comprobación pendiente")

        mensaje = (
            f"{etiqueta}\n"
            f"{bloque_descuento}\n"
            f"🏪 <b>{html.escape(tienda)}</b>\n"
            f"🛒 {html.escape(titulo)}\n"
            + (f"🏷️ Marca: <b>{html.escape(str(marca))}</b>\n" if marca else "")
            + (f"📂 Categoría: {html.escape(str(categoria))}\n" if categoria else "")
            + f"⭐ Puntuación: <b>{puntuacion}/100</b>\n"
            + (f"✨ {' · '.join(extras)}\n" if extras else "")
            + "\n"
            + f"💰 Ahora: ${actual:,.2f} MXN\n"
            + referencia_texto
            + ahorro_texto
            + (
                f"🏪 Sucursal: <b>{html.escape(str(item.get('sucursal') or tienda))}</b>\n"
                f"📍 {html.escape(str(item.get('direccion') or 'Ubicación de sucursal'))}\n"
                f"🔎 <a href=\"{html.escape(url, quote=True)}\">VER EVIDENCIA PÚBLICA</a>"
                if es_fisica
                else f"🔗 <a href=\"{html.escape(url, quote=True)}\">VER PRODUCTO DIRECTO</a>"
            )
        )
        avisos.append((clave, actual, mensaje))

    avisos.sort(key=lambda row: historial.get(row[0], {}).get("puntuacion", 0), reverse=True)

    enviados = 0
    errores_telegram = 0
    for clave, actual, mensaje in avisos:
        try:
            enviar_telegram(mensaje)
            historial[clave]["precio_alertado"] = actual
            enviados += 1
        except Exception as error:
            errores_telegram += 1
            print(f"ERROR enviando a Telegram para {clave}: {error}")

    guardar_historial(historial)
    print(
        f"[{datetime.now().isoformat()}] Candidatos: {len(vistos_en_esta_revision)} "
        f"| Candidatos de alerta: {len(avisos)} | Avisos enviados: {enviados} "
        f"| Errores Telegram: {errores_telegram}"
    )

if __name__ == "__main__":
    revisar()
