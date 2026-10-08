# monitor_ofertas.py
import html
import json
import os
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

# Migración estratégica a curl_cffi para mantener firmas JA3 indetectables en el orquestador
from curl_cffi import requests as curl_requests

from core.liquidation_engine import evaluate_product
from scrapers.tiendas_mexico import buscar_todas
from scrapers.telegram_ofertas import buscar_telegram
from scrapers.tiendas_fisicas import buscar_tiendas_fisicas
from scrapers.liquidaciones_oficiales import buscar_liquidaciones_oficiales
from core.extreme_liquidation import analizar_precio_extremo
from core.offer_identity import canonical_store, deduplicate_candidates, identity_keys, history_key
from core.product_identifiers import canonical_product_identifier

# Integración nativa de nuestras nuevas capas de servicios robustas
from scrapers.api_stores import ApiStoresScraper
from scrapers.vtex_stores import VtexStoresScraper
from scrapers.whatsapp_channels import buscar_whatsapp
from scrapers.feeds_comunidad_api import parsear_feed_comunidad_espejo
from scrapers.comunidades_web import buscar_comunidades_web
from scrapers.liquidazona import buscar_liquidazona_walmart

# Configuración del motor de registro de eventos
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DexterH4ck.Orchestrator")

MIN_DESCUENTO = 50
MAX_DESCUENTO = 99
HISTORIAL_FILE = "historial_ofertas.json"
MAX_HISTORIAL = 10000
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

_ultimo_envio_telegram = 0.0

TIENDAS_OBJETIVO = (
    "Walmart MX", "Bodega Aurrera", "Chedraui", "Soriana",
    "Liverpool", "Amazon MX", "Mercado Libre MX", "Coppel",
    "Suburbia", "Oferstock",
)

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
        historial = dict(sorted(historial.items(), key=lambda par: par.get("ultima_actualizacion", ""), reverse=True)[:MAX_HISTORIAL])
    temporal = f"{HISTORIAL_FILE}.tmp"
    with open(temporal, "w", encoding="utf-8") as archivo:
        json.dump(historial, archivo, ensure_ascii=False, indent=2)
    os.replace(temporal, HISTORIAL_FILE)

def limpiar_titulo_producto(titulo, url=""):
    texto = html.unescape(str(titulo or ""))
    texto = re.sub(r"\s+", " ", texto).strip(" \t\r\n-–—|·")
    texto = re.split(
        r"\b(?:precio\s+(?:actual|final|de\s+oferta)|antes|ahorra|hasta\s+\d+\s+mensualidades?|mensualidades?\s+fijas?|precio\s+anterior|precio\s+regular)\b",
        texto, maxsplit=1, flags=re.I,
    )
    if isinstance(texto, list) and len(texto) > 0:
        texto = texto[0]
    texto = re.sub(r"(?:^|[|·–—-])\s*\$\s*[0-9][0-9,]*(?:\s+[0-9]{2})?(?:\.[0-9]{1,2})?", " ", str(texto))
    texto = re.sub(r"\$\s*[0-9][0-9,]*(?:\s+[0-9]{2})?(?:\.[0-9]{1,2})?", " ", texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip(" \t\r\n-–—|·,;:")
    letras = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü]{2,}", texto)
    if len("".join(letras)) < 5 and url:
        try:
            from urllib.parse import unquote
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
    if not host or host in ("t.me", "telegram.me", "://google.com", "google.com"):
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

def es_enlace_valido_sin_error(url: str) -> bool:
    """Valida que el link del producto no contenga errores estructurales."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if not parsed.scheme in ["http", "https"] or not parsed.netloc:
        return False
    path_lower = parsed.path.lower()
    if any(err in path_lower for err in ["null", "undefined", "{productid}"]):
        return False
    return True

def validar_y_filtrar_bomba(item: dict, historial_local: dict) -> bool:
    """
    Regla estricta del 95% al 99%: Se mandan una única vez si no existen 
    en el historial y el link del producto es completamente válido.
    """
    precio_actual = float(item.get("precio_actual") or item.get("price") or 0)
    precio_anterior = item.get("precio_anterior")
    descuento = item.get("descuento", 0)
    
    if not descuento and precio_anterior and float(precio_anterior) > precio_actual:
        descuento = round((1 - precio_actual / float(precio_anterior)) * 100)

    if 95 <= descuento <= 99:
        identificador_producto = item.get("id") or canonical_product_identifier(item.get("url", ""))
        tienda_canonica = canonical_store(item.get("tienda", ""))
        clave_historial = f"{tienda_canonica}_{identificador_producto}"
        
        if clave_historial in historial_local:
            logger.info(f"[BOMBA OMITIDA] Ya fue enviada previamente: {clave_historial}")
            return False
            
        if not es_enlace_valido_sin_error(item.get("url", "")):
            logger.warning(f"[BOMBA RECHAZADA] Enlace con errores: {item.get('url')}")
            return False
            
        logger.info(f"[💣 BOMBA EMITIDA] Nueva liquidación extrema única del {descuento}%")
        return True
    return True

def _candidato_de_tienda_objetivo(item):
    if str(item.get("tipo_fuente") or "").upper() == "FISICA":
        return True
    tienda = str(item.get("tienda") or item.get("store") or "").strip().lower()
    aliases = (
        ("walmart", "Walmart MX"),
        ("bodega aurrera", "Bodega Aurrera"),
        ("chedraui", "Chedraui"),
        ("soriana", "Soriana"),
        ("liverpool", "Liverpool"),
        ("amazon", "Amazon MX"),
        ("mercado libre", "Mercado Libre MX"),
        ("mercadolibre", "Mercado Libre MX"),
        ("coppel", "Coppel"),
        ("suburbia", "Suburbia"),
        ("oferstock", "Oferstock"),
    )
    if any(alias in tienda for alias, _ in aliases):
        return True
    origen = str(item.get("origen_link") or item.get("origen") or "").lower()
    return origen in ("telegram", "liquidazona", "whatsapp") and bool(item.get("url"))

def enviar_telegram(texto, imagen=None, sticker_id=None):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
    global _ultimo_envio_telegram
    
    session = curl_requests.Session(impersonate="chrome")
    espera = 3.2 - (time.monotonic() - _ultimo_envio_telegram)
    if espera > 0:
        time.sleep(espera)
        
    if sticker_id:
        try:
            session.post(f"https://telegram.org{TELEGRAM_TOKEN}/sendSticker", data={"chat_id": TELEGRAM_CHAT_ID, "sticker": sticker_id}, timeout=15)
        except Exception as exc:
            print(f"Telegram sticker error: {str(exc)}")
            
    endpoint = f"https://telegram.org{TELEGRAM_TOKEN}/sendPhoto" if imagen else f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    
    for intento in range(2):
        try:
            if imagen:
                response = session.post(endpoint, data={"chat_id": TELEGRAM_CHAT_ID, "photo": imagen, "caption": texto, "parse_mode": "HTML"}, timeout=25)
            else:
                response = session.post(endpoint, data={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": False}, timeout=20)
            
            _ultimo_envio_telegram = time.monotonic()
            if response.status_code == 200:
                return
            if response.status_code == 429:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", "5"))
                time.sleep(retry_after)
        except Exception:
            imagen = None
            endpoint = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"

def ejecutar_orquestacion_paralela(contexto_ficticio=None) -> list:
    """Orquestación Paralela: Consume microservicios internos e inyecta hilos concurrentes."""
    logger.info("Iniciando despacho asincrónico multihilo de scrapers de liquidación...")
