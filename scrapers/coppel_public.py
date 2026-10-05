"""Extractor público de Coppel basado en la ficha y __NEXT_DATA__.

No intenta saltar Cloudflare. Si Coppel devuelve una página accesible, extrae
los datos estructurados que el propio sitio entrega al navegador.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.product_identifiers import extract_coppel_context
from core.liquidation_engine import detect_priority_brand, infer_category


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def parse_next_data(html: str, url: str) -> dict[str, Any] | None:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html or "",
        re.I | re.S,
    )
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (TypeError, json.JSONDecodeError):
        return None


def extract_product_from_next_data(html: str, url: str) -> dict[str, Any] | None:
    context = extract_coppel_context(url)
    data = parse_next_data(html, url)
    if not data:
        return None

    candidates = []
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        title = node.get("name") or node.get("productName") or node.get("title")
        current = node.get("price") or node.get("salePrice") or node.get("sellingPrice")
        previous = (
            node.get("listPrice") or node.get("regularPrice") or
            node.get("originalPrice") or node.get("compareAtPrice")
        )
        if title and current is not None:
            candidates.append((str(title), current, previous, node))

    if not candidates:
        return None

    title, current, previous, node = candidates[0]
    try:
        current = float(str(current).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    try:
        previous = float(str(previous).replace("$", "").replace(",", "").strip()) if previous is not None else None
    except (TypeError, ValueError):
        previous = None

    brand = node.get("brand") or node.get("brandName") or ""
    if isinstance(brand, dict):
        brand = brand.get("name") or ""
    brand, _ = detect_priority_brand({"titulo": title, "marca": brand})
    category = infer_category({"titulo": title, "marca": brand, "categoria": node.get("category") or ""})
    discount = round((1 - current / previous) * 100) if previous and previous > current else 0

    return {
        "id": context.get("sku") or node.get("sku") or node.get("id"),
        "titulo": title,
        "nombre": title,
        "marca": brand,
        "categoria": category,
        "precio_actual": current,
        "precio_anterior": previous if previous and previous > current else None,
        "descuento": discount,
        "url": context["url"],
        "score": discount,
        "puntuacion": discount,
        "es_bomba": discount >= 90,
        "tienda": "Coppel",
        "sku": context.get("sku", ""),
        "origen_link": "coppel_next_data",
    }
