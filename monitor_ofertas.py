import html
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from core.liquidation_engine import evaluate_product
from scrapers.tiendas_mexico import buscar_todas
from scrapers.telegram_ofertas import buscar_telegram
from scrapers.tiendas_fisicas import buscar_tiendas_fisicas
from scrapers.liquidaciones_oficiales import buscar_liquidaciones_oficiales
from core.extreme_liquidation import analizar_precio_extremo

MIN_DESCUENTO = 40
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




def limpiar_titulo_producto(titulo, url=""):
    """Limpia títulos contaminados por precios/metadatos de tarjetas de tienda."""
    from urllib.parse import unquote, urlparse
    texto = html.unescape(str(titulo or ""))
    texto = re.sub(r"\s+", " ", texto).strip(" \t\r\n-–—|·")
    texto = re.split(
        r"\b(?:precio\s+(?:actual|final|de\s+oferta)|antes|ahorra|hasta\s+\d+\s+mensualidades?|mensualidades?\s+fijas?|precio\s+anterior|precio\s+regular)\b",
        texto, maxsplit=1, flags=re.I,
    )[0]
    texto = re.sub(r"(?:^|[|·–—-])\s*\$\s*[0-9][0-9,]*(?:\s+[0-9]{2})?(?:\.[0-9]{1,2})?", " ", texto)
    texto = re.sub(r"\$\s*[0-9][0-9,]*(?:\s+[0-9]{2})?(?:\.[0-9]{1,2})?", " ", texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip(" \t\r\n-–—|·,;:")
    letras = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü]{2,}", texto)
    if len("".join(letras)) < 5 and url:
        try:
            path = unquote(urlparse(url).path).rstrip("/")
            slug = path.rsplit("/", 1)[-1]
            slug = re.sub(r"(?:-)?(?:mlm[-_]?)?\d{5,}$", "", slug, flags=re.I)
            slug = re.sub(r"[-_]+", " ", slug)
            slug = re.sub(r"\b(?:ip|pdp|producto|product|item)\b", " ", slug, flags=re.I)
            slug = re.sub(r"\s{2,}", " ", slug).strip(" -_/")
            if len(slug) >= 5:
                texto = slug
        except Exception:
            pass
    return texto[:180]

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
        "chedraui.com.mx": ("/p/",),
        "coppel.com": ("/pdp/",),
        "amazon.com.mx": ("/dp/", "/gp/product/"),
        "mercadolibre.com.mx": ("/mlm-", "/p/"),
        "liverpool.com.mx": ("/tienda/pdp/", "/pdp/"),
        "soriana.com": ("/producto/", "/p/"),
        "suburbia.com.mx": ("/p/", "/producto/"),
    }
    for dominio, patrones in patrones_por_tienda.items():
        if host == dominio or host.endswith("." + dominio):
            if dominio == "chedraui.com.mx":
                return "/p/" in path or path.endswith("/p")
            if dominio == "soriana.com" and re.search(r"/\d{5,}\.html$", path):
                return True
            return any(p in path for p in patrones)
    return len(path.strip("/")) > 12

_ultimo_envio_telegram = 0.0

def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
    global _ultimo_envio_telegram
    import time
    # Un solo flujo de salida y ~3.2 s entre mensajes evita el límite de grupo.
    espera = 3.2 - (time.monotonic() - _ultimo_envio_telegram)
    if espera > 0:
        time.sleep(espera)
    for intento in range(2):
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=20,
        )
        _ultimo_envio_telegram = time.monotonic()
        if response.ok:
            return
        if response.status_code == 429:
            try:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", "5"))
            except (ValueError, TypeError):
                retry_after = 5
            print(f"Telegram 429: esperando {retry_after}s antes de reintentar.")
            if intento == 0:
                time.sleep(min(max(retry_after, 1), 120) + 0.5)
                continue
        raise RuntimeError(f"Telegram rechazó el mensaje: HTTP {response.status_code} - {response.text}")

def calcular_datos(item, anterior_hist):
    actual = float(item.get("precio_actual") or item.get("price") or 0)
    listado = float(item["precio_anterior"]) if item.get("precio_anterior") else None
    # La referencia publicada por la tienda tiene prioridad. El historial
    # solo sirve como respaldo cuando la ficha actual no trae precio anterior.
    historica = float(anterior_hist.get("precio_maximo") or 0)
    referencia = listado if listado and listado > actual else (historica if historica > actual else 0)
    dcto = round((1 - actual / referencia) * 100) if referencia > actual else 0
    return actual, referencia, dcto

def revisar():
    historial = cargar_historial()
    avisos = []
    vistos_en_esta_revision = set()

    # El monitor principal NO reenvía ofertas históricas ni rearma alertas.
    # La republicación se realiza exclusivamente mediante el workflow manual
    # reenviar_ofertas_hoy.yml / reenviar_ofertas_hoy.py.
    rearmar_alertas = False

    candidatos = buscar_todas()
    candidatos.extend(buscar_telegram(requests.Session()))
    candidatos.extend(buscar_tiendas_fisicas(requests.Session()))
    candidatos.extend(buscar_liquidaciones_oficiales(requests.Session()))
    # Salud de descubrimiento: permite comprobar que las tiendas no queden
    # monopolizadas por 95/99 y que también estén llegando rangos medios.
    salud_tiendas = {}
    for candidato in candidatos:
        tienda_salud = str(candidato.get("tienda") or candidato.get("store") or "Desconocida")
        actual_salud = float(candidato.get("precio_actual") or candidato.get("price") or 0)
        referencia_salud = float(candidato.get("precio_anterior") or candidato.get("previous_price") or 0)
        if actual_salud <= 0:
            continue
        descuento_salud = round((1 - actual_salud / referencia_salud) * 100) if referencia_salud > actual_salud else 0
        registro_salud = salud_tiendas.setdefault(tienda_salud, {
            "candidatos": 0,
            "con_referencia": 0,
            "sin_referencia": 0,
            "rangos_descuento": {
                "40-49": 0,
                "50-69": 0,
                "70-89": 0,
                "90-94": 0,
                "95-99": 0,
                "sin_descuento_comparable": 0,
            },
            "descubrimiento_publico": 0,
        })
        registro_salud["candidatos"] += 1
        if referencia_salud > actual_salud:
            registro_salud["con_referencia"] += 1
            if 40 <= descuento_salud <= 49:
                registro_salud["rangos_descuento"]["40-49"] += 1
            elif 50 <= descuento_salud <= 69:
                registro_salud["rangos_descuento"]["50-69"] += 1
            elif 70 <= descuento_salud <= 89:
                registro_salud["rangos_descuento"]["70-89"] += 1
            elif 90 <= descuento_salud <= 94:
                registro_salud["rangos_descuento"]["90-94"] += 1
            elif 95 <= descuento_salud <= 99:
                registro_salud["rangos_descuento"]["95-99"] += 1
            else:
                registro_salud["rangos_descuento"]["sin_descuento_comparable"] += 1
        else:
            registro_salud["sin_referencia"] += 1
        if str(candidato.get("origen_link") or "").lower() == "buscador_publico":
            registro_salud["descubrimiento_publico"] += 1

    with open("preflight_health.json", "w", encoding="utf-8") as health_file:
        json.dump({
            "politica_publicacion": {
                "min_descuento_comparable": MIN_DESCUENTO,
                "max_descuento_comparable": MAX_DESCUENTO,
                "rangos_incluidos": ["5-49", "50-69", "70-89", "90-94", "95-99"],
            },
            "tiendas": salud_tiendas,
        }, health_file, ensure_ascii=False, indent=2)

    print(f"Salud de fuentes por tienda: {json.dumps(salud_tiendas, ensure_ascii=False)}")
    print(f"Fuentes adicionales Telegram + físicas + liquidaciones oficiales: {len(candidatos)} candidatos totales")
    descartes = {
        "sin_titulo_o_url": 0,
        "duplicado": 0,
        "sin_precio": 0,
        "sin_ficha_directa": 0,
        "sin_referencia": 0,
        "descuento_menor_5": 0,
        "descuento_mayor_99": 0,
        "historico_ya_alertado": 0,
        "no_elegible": 0,
    }
    for item in candidatos:
        url = str(item.get("url") or "").strip()
        titulo = limpiar_titulo_producto(item.get("titulo") or item.get("title") or item.get("nombre") or "", url)
        tienda = str(item.get("tienda") or item.get("store") or "Desconocida").strip()
        if not titulo or not url:
            descartes["sin_titulo_o_url"] += 1
            continue

        clave = item.get("id") or f"{tienda}|{titulo}|{url}"
        if clave in vistos_en_esta_revision:
            descartes["duplicado"] += 1
            continue
        vistos_en_esta_revision.add(clave)

        anterior_hist = historial.get(clave, {})
        actual, referencia, dcto = calcular_datos(item, anterior_hist)
        if actual <= 0:
            descartes["sin_precio"] += 1
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
            historial[clave]["titulo"] = titulo
            historial[clave]["descuento"] = dcto
            historial[clave]["puntuacion"] = scoring["puntuacion"]
            historial[clave]["extremo"] = extreme
            if scoring.get("marca"):
                historial[clave]["marca"] = scoring["marca"]
            if scoring.get("categoria"):
                historial[clave]["categoria"] = scoring["categoria"]

        ultimo_alertado = anterior_hist.get("precio_alertado")
        if not rearmar_alertas and ultimo_alertado is not None and actual >= float(ultimo_alertado):
            descartes["historico_ya_alertado"] += 1
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
        if not tiene_precio:
            descartes["sin_precio"] += 1
            continue
        if not es_enlace:
            descartes["sin_ficha_directa"] += 1
            continue
        if not tiene_referencia:
            descartes["sin_referencia"] += 1
            continue
        if dcto < MIN_DESCUENTO:
            descartes["descuento_menor_5"] += 1
            continue
        if dcto > MAX_DESCUENTO:
            descartes["descuento_mayor_99"] += 1
            continue

        marca = scoring.get("marca") or item.get("marca")
        categoria = scoring.get("categoria") or item.get("categoria") or "Otros / Miscelánea"
        puntuacion = scoring.get("puntuacion", 0)

        extremo = analizar_precio_extremo(item)
        precio_extremo = extremo.get("es_extremo", False)
        nivel_extremo = extremo.get("nivel", "normal")
        precio_extremo_verificado = extremo.get("precio_verificado", False)
        condiciones = item.get("condiciones") or []

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
        if condiciones:
            extras.append("🎟️ " + ", ".join(str(x) for x in condiciones))

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

    print(f"Descartes: {json.dumps(descartes, ensure_ascii=False)}")
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
