# scrapers/tiendas_mexico.py
import hashlib
import logging
import re
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.source_resilience import BLOCK_SIGNATURES, HONEST_USER_AGENT, classify_status

logger = logging.getLogger("DexterH4ck.TiendasMexico")

# Estado de la última corrida por tienda (lo lee el monitor para reportar bloqueos reales).
LAST_STATUS: dict = {}

TERMINOS = ["liquidacion", "ofertas", "remate", "outlet"]

# Búsquedas públicas por tienda.
URLS_BUSQUEDA = {
    "amazon": "https://www.amazon.com.mx/s?k={q}",
    "soriana": "https://www.soriana.com/buscar?q={q}",
    "liverpool": "https://www.liverpool.com.mx/tienda?s={q}",
}


def _hash_id(*partes) -> str:
    return hashlib.sha1("|".join(str(p) for p in partes).encode("utf-8")).hexdigest()[:16]


def buscar_todas(filtro_tienda=None) -> list:
    """Motor legacy: rastrea búsquedas públicas cuando las APIs dedicadas no están disponibles.

    Si una tienda responde 403/429/5xx o muestra un reto, se registra el estado real en
    LAST_STATUS y se deja de consultar ESA tienda en este ciclo; las demás continúan.
    """
    productos_encontrados = []
    LAST_STATUS.clear()

    session = requests.Session()
    session.headers.update({
        "User-Agent": HONEST_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
    })

    tiendas = [filtro_tienda.lower()] if filtro_tienda else list(URLS_BUSQUEDA)
    for tienda in tiendas:
        plantilla = URLS_BUSQUEDA.get(tienda)
        if not plantilla:
            continue
        LAST_STATUS[tienda] = "ok"
        for termino in TERMINOS:
            target_url = plantilla.format(q=quote_plus(termino))
            try:
                logger.info("[%s] Extrayendo listado público: %s", tienda.upper(), target_url)
                response = session.get(target_url, timeout=25.0)
                estado = classify_status(response.status_code)
                if estado == "ok":
                    cuerpo = (response.text or "").lower()
                    if any(sig in cuerpo for sig in BLOCK_SIGNATURES):
                        LAST_STATUS[tienda] = "blocked"
                        logger.warning("[%s] Reto/bloqueo detectado; se omite la tienda en este ciclo.", tienda.upper())
                        break
                    productos_encontrados.extend(_parsear_html_por_tienda(response.text, tienda, target_url))
                else:
                    LAST_STATUS[tienda] = estado
                    logger.warning("[%s] HTTP %s (%s); se omite la tienda en este ciclo.",
                                   tienda.upper(), response.status_code, estado)
                    # 403/429/5xx/etc.: no se insiste con más términos contra la misma tienda.
                    break
            except requests.Timeout:
                LAST_STATUS[tienda] = "timeout"
                logger.error("[%s] Timeout; se omite la tienda en este ciclo.", tienda.upper())
                break
            except requests.RequestException as exc:
                LAST_STATUS[tienda] = "network_error"
                logger.error("[%s] Error de red: %s", tienda.upper(), type(exc).__name__)
                break

    return productos_encontrados


def _parsear_html_por_tienda(html_source, tienda, origen_url) -> list:
    """Análisis sintáctico básico del HTML extraído."""
    soup = BeautifulSoup(html_source, "html.parser")
    resultados = []

    if tienda == "amazon":
        for contenedor in soup.select("div[data-component-type='s-search-result']"):
            try:
                titulo_el = contenedor.select_one("h2 a span")
                link_el = contenedor.select_one("h2 a")
                precio_el = contenedor.select_one(".a-price-whole")
                if titulo_el and link_el and precio_el:
                    precio_actual = float(re.sub(r"[^0-9.]", "", precio_el.text.replace(",", "")) or 0)
                    url_final = urljoin("https://www.amazon.com.mx", link_el.get("href", ""))
                    resultados.append({
                        "id": contenedor.get("data-asin") or _hash_id(url_final),
                        "name": titulo_el.text.strip(),
                        "titulo": titulo_el.text.strip(),
                        "brand": "Amazon",
                        "url": url_final,
                        "precio_actual": precio_actual,
                        "precio_anterior": None,  # la referencia sale del historial JSON
                        "tienda": "Amazon MX",
                        "tipo_fuente": "WEB_FALLBACK",
                    })
            except (ValueError, AttributeError, TypeError):
                continue

    elif tienda == "soriana":
        for card in soup.select(".product-card"):
            try:
                link_el = card.select_one("a.product-title-link")
                precio_el = card.select_one(".value")
                if link_el and precio_el:
                    precio_actual = float(precio_el.text.replace("$", "").replace(",", "").strip())
                    url_final = urljoin("https://www.soriana.com", link_el.get("href", ""))
                    resultados.append({
                        "id": card.get("data-pid") or _hash_id(url_final),
                        "name": link_el.text.strip(),
                        "titulo": link_el.text.strip(),
                        "brand": "Soriana",
                        "url": url_final,
                        "precio_actual": precio_actual,
                        "precio_anterior": None,
                        "tienda": "Soriana",
                        "tipo_fuente": "WEB_FALLBACK",
                    })
            except (ValueError, AttributeError, TypeError):
                continue

    return resultados


def es_url_producto(url, base_url=None):
    """Determina si una URL pertenece a una página de producto y no a un listado/búsqueda."""
    try:
        parsed = urlparse(url or "")
        host = parsed.netloc.lower().split(":", 1)[0]
        path = parsed.path.lower()
        if not host or not path or path == "/":
            return False
        if base_url:
            base_host = urlparse(base_url).netloc.lower().split(":", 1)[0]
            # www.bodegaaurrera.com.mx y despensa.bodegaaurrera.com.mx son la misma tienda:
            # se compara contra el dominio sin el prefijo "www.".
            if base_host.startswith("www."):
                base_host = base_host[4:]
            if base_host and not (host == base_host or host.endswith("." + base_host)):
                return False
        if any(x in path for x in ("/search", "/buscar", "/ofertas", "/oferta", "/catalogo", "/marcas", "/home", "/social/")):
            return False
        patrones = (
            "/ip/", "/dp/", "/gp/product/", "/mlm-", "/p/", "/pdp/",
            "/tienda/pdp/", "/producto/", "/product/", "/item/"
        )
        if any(p in path for p in patrones):
            return True
        # Amazon y Mercado Libre también pueden usar identificadores en la ruta.
        if re.search(r"/(?:dp|gp/product|itm|mlm)[-_]?[a-z0-9]+", path):
            return True
        return False
    except Exception:
        return False
