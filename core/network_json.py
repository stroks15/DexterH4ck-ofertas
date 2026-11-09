"""Motor JSON-first para catálogos de comercio electrónico.

Convierte respuestas JSON estructuradas en el contrato común del monitor sin
depender de selectores CSS ni del HTML visual. No intenta saltar CAPTCHA/WAF,
rotar proxies ni falsificar credenciales.
"""

from __future__ import annotations
import re
from typing import Any
from urllib.parse import urljoin

from core.price_sanitizer import parse_price, sanear_par_precios

CURRENT_KEYS = (
    "price",
    "currentPrice",
    "salePrice",
    "sellingPrice",
    "offerPrice",
    "precio",
    "precioActual",
    "finalPrice",
    "salesPrice",
)
REFERENCE_KEYS = (
    "originalPrice",
    "listPrice",
    "regularPrice",
    "compareAtPrice",
    "wasPrice",
    "previousPrice",
    "precioAnterior",
    "precioRegular",
    "priceBeforeDiscount",
    "list_price",
    "regular_price",
)
TITLE_KEYS = ("name", "title", "productName", "productTitle", "nombre", "displayName")
URL_KEYS = ("url", "link", "permalink", "canonicalUrl", "productUrl", "productURL")
ID_KEYS = ("id", "productId", "productID", "sku", "itemId", "upc", "gtin")


def number(value: Any) -> float | None:
    """Precio estricto: solo números reales o montos limpios ("$1,299.00").

    Antes se extraía el primer número de cualquier texto, de modo que "875g" o "1 kg"
    terminaban como precio. Ahora cualquier cadena con unidades se rechaza.
    """
    return parse_price(value)


def _first(node, keys):
    for key in keys:
        if key in node and node[key] not in (None, ""):
            return node[key]
    return None


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _nested_price(node, keys):
    direct = number(_first(node, keys))
    if direct is not None:
        return direct
    for container_key in ("priceInfo", "prices", "priceData", "pricing", "offer", "offers"):
        container = node.get(container_key)
        if isinstance(container, dict):
            value = number(_first(container, keys))
            if value is not None:
                return value
        elif isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    value = number(_first(item, keys))
                    if value is not None:
                        return value
    return None


def discount(current, reference):
    if not current or not reference or reference <= current:
        return 0
    return max(0, min(99, round((1 - current / reference) * 100)))




def _price_pair(node: dict) -> tuple[float | None, float | None]:
    """Obtiene actual/referencia del mismo nodo de precio u oferta.

    Evita combinar el precio actual de un seller/variante con el precio de lista
    de otro elemento de una lista ``offers`` o ``prices``.
    """
    if not isinstance(node, dict):
        return None, None

    current = number(_first(node, CURRENT_KEYS))
    if current is not None:
        reference = number(_first(node, REFERENCE_KEYS))
        return current, reference if reference is not None and reference > current else None

    candidates: list[tuple[float, float | None]] = []
    for key in ("priceInfo", "prices", "priceData", "pricing", "offer", "offers"):
        nested = node.get(key)
        if isinstance(nested, dict):
            pair = _price_pair(nested)
            if pair[0] is not None:
                candidates.append(pair)
        elif isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    pair = _price_pair(item)
                    if pair[0] is not None:
                        candidates.append(pair)

    if not candidates:
        return None, None
    return max(candidates, key=lambda pair: (discount(pair[0], pair[1]), -pair[0]))


def extract_products(payload: Any, base_url="", store="JSON"):
    output = []
    seen = set()
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        title = _first(node, TITLE_KEYS)
        current, reference = _price_pair(node)
        url = _first(node, URL_KEYS)
        if not title or current is None or not url:
            continue
        product_url = urljoin(base_url, str(url).strip())
        if not product_url.startswith(("http://", "https://")):
            continue
        current, reference, _ = sanear_par_precios(
            current,
            reference,
            f"{title} {node.get('description') or ''} {node.get('size') or ''}",
        )
        if current is None:
            continue
        product_id = _first(node, ID_KEYS)
        brand = _first(node, ("brand", "brandName", "marca"))
        if isinstance(brand, dict):
            brand = brand.get("name") or brand.get("value") or ""
        category = _first(node, ("category", "categoryName", "categoryPath", "categoria"))
        if isinstance(category, list):
            category = category[-1] if category else ""
        identity = str(product_id or product_url).strip().lower()
        if identity in seen:
            continue
        seen.add(identity)
        output.append(
            {
                "id": product_id or product_url,
                "product_id": str(product_id or ""),
                "titulo": str(title).strip()[:180],
                "nombre": str(title).strip()[:180],
                "marca": str(brand or ""),
                "categoria": str(category or ""),
                "precio_actual": current,
                "precio_anterior": reference,
                "descuento": discount(current, reference),
                "url": product_url,
                "tienda": store,
                "origen_link": "network_json",
                "api": "network_json",
            }
        )
    return output
