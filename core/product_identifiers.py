"""Identificadores y normalización de URLs de producto.

Funciones deterministas para reutilizar URLs de afiliados, fichas de producto y
enlaces cortos sin depender del HTML visual. No realizan evasión de WAF/CAPTCHA.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse


def unwrap_affiliate_url(url: str) -> str:
    """Desenvuelve parámetros habituales de afiliación (dl/url/redirect)."""
    current = str(url or "").strip()
    for _ in range(3):
        parsed = urlparse(current)
        query = parse_qs(parsed.query)
        candidate = None
        for key in ("dl", "url", "redirect", "redirect_url", "destination", "dest"):
            values = query.get(key)
            if values:
                candidate = unquote(values[0])
                break
        if not candidate or candidate == current:
            break
        current = candidate
    return current


def extract_walmart_context(url: str) -> dict[str, str]:
    """Extrae UPC/GTIN de /ip/.../<digits> y wl13 cuando existe."""
    parsed = urlparse(str(url or ""))
    path = parsed.path
    match = re.search(r"/ip/[^/]+/([0-9]{8,14})(?:/)?$", path, re.I)
    upc = match.group(1) if match else ""
    store_id = (parse_qs(parsed.query).get("wl13") or [""])[0]
    return {
        "product_id": upc,
        "upc": upc,
        "store_id": store_id,
        "url": str(url or "").strip(),
    }


def extract_bodega_context(url: str) -> dict[str, str]:
    data = extract_walmart_context(url)
    data["url"] = str(url or "").strip()
    return data


def extract_coppel_context(url: str) -> dict[str, str]:
    clean = unwrap_affiliate_url(url)
    parsed = urlparse(clean)
    path = unquote(parsed.path)
    # Coppel usa /pdp/<slug>-pr-<SKU> y puede cambiar la longitud del SKU.
    match = re.search(r"(?:^|-)pr-([0-9]{5,})(?:/)?$", path, re.I)
    if not match:
        match = re.search(r"(?:^|/)([0-9]{6,})(?:/)?$", path)
    sku = match.group(1) if match else ""
    return {"url": clean, "sku": sku, "product_id": sku}


def extract_amazon_context(url: str) -> dict[str, str]:
    parsed = urlparse(str(url or ""))
    path = parsed.path
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", path, re.I)
    asin = match.group(1).upper() if match else ""
    return {"url": str(url or "").strip(), "asin": asin, "product_id": asin}


def extract_mercadolibre_id(url: str) -> str:
    """Extrae MLM... si ya está expandido; no hace redirecciones de red."""
    parsed = urlparse(str(url or ""))
    text = f"{parsed.path} {parsed.query}"
    patterns = (
        r"\b(MLM[-_]?[0-9]{6,12})\b",
        r"(?:/p/|/MLM-)([0-9]{6,12})(?:[/?]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = match.group(1).upper().replace("-", "").replace("_", "")
            return value if value.startswith("MLM") else f"MLM{value}"
    return ""


def canonical_product_identifier(store: str, url: str) -> dict[str, str]:
    name = str(store or "").lower()
    if "walmart" in name:
        return extract_walmart_context(url)
    if "bodega" in name:
        return extract_bodega_context(url)
    if "coppel" in name:
        return extract_coppel_context(url)
    if "amazon" in name:
        return extract_amazon_context(url)
    if "mercado" in name:
        return {"url": str(url or "").strip(), "product_id": extract_mercadolibre_id(url)}
    return {"url": str(url or "").strip(), "product_id": ""}


def expand_mercadolibre_url(url: str, session=None) -> str:
    """Expande enlaces cortos con HEAD; devuelve la URL original ante fallo."""
    import requests
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        session = session or requests.Session()
        response = session.head(value, allow_redirects=True, timeout=10)
        return response.url or value
    except requests.RequestException:
        return value
