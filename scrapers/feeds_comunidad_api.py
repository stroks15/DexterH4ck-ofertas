"""Fuente comunitaria RSS de ofertas mexicanas.

Se usa como contingencia cuando las tiendas oficiales devuelven 403/429/503.
No intenta evadir WAF ni acceder a endpoints privados: consume únicamente
el RSS público y conserva el enlace de la publicación comunitaria cuando no
existe un enlace directo al producto.
"""

from __future__ import annotations

import html
import logging
import os
import re
from urllib.parse import urlparse

import feedparser
import requests

LOG = logging.getLogger("feeds_comunidad_api")

DEFAULT_FEED_URL = "https://promodescuentos.com/rss"
PRICE_RE = re.compile(r"\$\s*([0-9][0-9,.]*)")
DISCOUNT_RE = re.compile(r"(?i)(?:descuento|rebaja|ahorra|off)\D{0,12}(\d{2})\s*%|(?<!\d)([5-9]\d)\s*%")
REFERENCE_RE = re.compile(r"(?i)(?:antes|de\s+\$|era|normal)\s*[:$]?\s*\$?\s*([0-9][0-9,.]*)")


def _money(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def _store(text: str) -> str | None:
    low = text.lower()
    if "amazon" in low:
        return "Amazon MX"
    if "coppel" in low:
        return "Coppel"
    if "walmart" in low:
        return "Walmart MX"
    if "bodega aurrera" in low or re.search(r"\bbodega\b", low):
        return "Bodega Aurrera"
    if "mercado libre" in low or "mercadolibre" in low:
        return "Mercado Libre MX"
    if "liverpool" in low:
        return "Liverpool"
    if "suburbia" in low:
        return "Suburbia"
    if "soriana" in low:
        return "Soriana"
    if "física" in low or "fisica" in low or "tienda física" in low:
        return "Liquidación Física"
    return None


def _direct_store_link(description: str, store: str | None) -> str | None:
    links = re.findall(r"https?://[^\s\"'<>]+", description or "")
    allowed = {
        "Amazon MX": ("amazon.com.mx",),
        "Coppel": ("coppel.com",),
        "Walmart MX": ("walmart.com.mx",),
        "Bodega Aurrera": ("bodegaaurrera.com.mx",),
        "Mercado Libre MX": ("mercadolibre.com.mx",),
        "Liverpool": ("liverpool.com.mx",),
        "Suburbia": ("suburbia.com.mx",),
        "Soriana": ("soriana.com",),
    }
    for link in links:
        host = urlparse(link).netloc.lower()
        if any(host == d or host.endswith("." + d) for d in allowed.get(store, ())):
            return link.rstrip(").,;")
    return None


def parsear_feed_comunidad_espejo() -> list[dict]:
    url = os.getenv("PROMODESCUENTOS_RSS_URL", DEFAULT_FEED_URL)
    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        LOG.warning("[FEED ALTERNATIVO] %s: %s", type(exc).__name__, exc)
        return []

    if getattr(feed, "bozo", False):
        LOG.warning("[FEED ALTERNATIVO] RSS con advertencia de parseo: %s", getattr(feed, "bozo_exception", "desconocida"))

    output = []
    seen = set()
    for entry in getattr(feed, "entries", []):
        title = _clean(getattr(entry, "title", ""))
        description = _clean(getattr(entry, "description", "") or getattr(entry, "summary", ""))
        combined = f"{title} {description}"
        store = _store(combined)
        if not store:
            continue

        community_url = str(getattr(entry, "link", "") or "").strip()
        direct_url = _direct_store_link(description, store)
        url = direct_url or community_url
        if not url or url in seen:
            continue
        seen.add(url)

        prices = [_money(x) for x in PRICE_RE.findall(combined)]
        prices = [x for x in prices if x is not None and x > 0]
        current = min(prices) if prices else None

        match = DISCOUNT_RE.search(combined)
        discount = int(match.group(1) or match.group(2)) if match else 0

        reference_match = REFERENCE_RE.search(combined)
        reference = _money(reference_match.group(1)) if reference_match else None
        if current and reference and reference > current:
            discount = round((1 - current / reference) * 100)

        # Nunca asignamos artificialmente 50% solo por aparecer en el feed.
        # La comunidad es evidencia auxiliar, no una prueba de descuento.
        if not current or not (50 <= discount <= 99):
            continue

        output.append({
            "id": f"feed:{getattr(entry, 'id', '') or community_url}",
            "nombre": title,
            "titulo": title,
            "marca": "",
            "precio_actual": current,
            "precio_anterior": reference,
            "descuento": discount,
            "url": url,
            "score": min(100, discount + 10),
            "puntuacion": min(100, discount + 10),
            "es_bomba": discount >= 90,
            "tienda": store,
            "origen_link": "feed_comunidad",
            "url_evidencia_comunidad": community_url,
            "tipo_fuente": "COMUNIDAD",
        })

    LOG.info("[FEED ALTERNATIVO] %d alertas válidas", len(output))
    return output
