"""Fuentes oficiales de liquidación/outlet.

Estas fuentes no sustituyen a los scrapers de tienda. Se usan para encontrar
productos que la propia tienda clasifica como liquidación y después pasan por
el mismo motor de verificación.
"""

import json
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.liquidation_engine import detect_priority_brand, infer_category

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
}

FUENTES = [
    {
        "tienda": "Walmart MX",
        "url": "https://kiosco.www.walmart.com.mx/shop/liquidaciones-walmart",
        "host": "walmart.com.mx",
        "patrones": ("/ip/",),
    },
    {
        "tienda": "Chedraui",
        "url": "https://www.chedraui.com.mx/precios-liquidacion",
        "host": "chedraui.com.mx",
        "patrones": ("/p/",),
    },
    {
        "tienda": "Sanborns",
        "url": "https://www.sanborns.com.mx/cat/videojuegos?id=12&percent_off=50+TO+80&order=sanborns_price_asc",
        "host": "sanborns.com.mx",
        "patrones": ("/producto/", "/p/", "/item/"),
    },
    {
        "tienda": "Juguetron",
        "url": "https://www.juguetron.mx/promociones",
        "host": "juguetron.mx",
        "patrones": ("/p/", ".html"),
    },
]

PRICE_RE = re.compile(
    r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)"
)


def _prices(text):
    values = []
    for value in PRICE_RE.findall(text or ""):
        try:
            number = float(value.replace(",", ""))
            if 0 < number <= 2_000_000:
                values.append(number)
        except ValueError:
            pass
    return values


def _is_product(url, source):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not host or not (host == source["host"] or host.endswith("." + source["host"])):
        return False
    if any(x in path for x in ("/search", "/buscar", "/catalogo", "/ofertas", "/marcas", "/home", "/precios-liquidacion", "/promociones")):
        return False
    return any(p in path for p in source["patrones"]) or (source["host"] == "chedraui.com.mx" and path.endswith("/p"))


def _title_from(node):
    for selector in ("h2", "h3", "h4", "[class*='title']", "[class*='name']"):
        found = node.select_one(selector)
        if found:
            text = found.get_text(" ", strip=True)
            if len(text) >= 6:
                return text[:220]
    text = node.get_text(" ", strip=True)
    return text[:220]


def _from_json_ld(soup, source, seen):
    results = []
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") not in ("Product", "ProductGroup"):
                continue
            url = node.get("url") or ""
            if url and not url.startswith("http"):
                url = urljoin(source["url"], url)
            if not _is_product(url, source):
                continue
            title = str(node.get("name") or "").strip()
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            try:
                price = float(offers.get("price")) if offers.get("price") is not None else None
            except (TypeError, ValueError):
                price = None
            if not title or not price or url in seen:
                continue
            seen.add(url)
            brand_value = node.get("brand")
            if isinstance(brand_value, dict):
                brand_value = brand_value.get("name")
            brand, _ = detect_priority_brand({"titulo": title, "marca": brand_value or ""})
            results.append({
                "tienda": source["tienda"],
                "titulo": title,
                "marca": brand,
                "categoria": infer_category({"titulo": title, "marca": brand}),
                "precio_actual": price,
                "precio_anterior": None,
                "descuento": 0,
                "url": url,
                "liquidacion": True,
                "outlet": True,
                "tipo_fuente": "LIQUIDACION_OFICIAL",
                "origen": source["url"],
                "origen_link": source["url"],
            })
    return results


def _from_links(soup, source, seen):
    results = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(source["url"], anchor.get("href", ""))
        if not _is_product(href, source) or href in seen:
            continue

        node = anchor
        for _ in range(4):
            if node.parent:
                node = node.parent
            text = node.get_text(" ", strip=True)
            if len(text) >= 20 and _prices(text):
                break

        prices = _prices(text)
        if not prices:
            continue

        title = _title_from(node)
        if not title or len(title) < 8:
            continue

        actual = min(prices)
        bigger = [p for p in prices if p > actual]
        previous = min(bigger) if bigger else None
        brand, _ = detect_priority_brand({"titulo": title})
        seen.add(href)
        results.append({
            "tienda": source["tienda"],
            "titulo": title,
            "marca": brand,
            "categoria": infer_category({"titulo": title, "marca": brand}),
            "precio_actual": actual,
            "precio_anterior": previous,
            "descuento": round((1 - actual / previous) * 100) if previous and previous > actual else 0,
            "url": href,
            "liquidacion": True,
            "outlet": True,
            "tipo_fuente": "LIQUIDACION_OFICIAL",
            "origen": source["url"],
            "origen_link": source["url"],
        })
    return results


def buscar_liquidaciones_oficiales(session=None):
    session = session or requests.Session()
    session.headers.update(HEADERS)
    resultados = []

    for source in FUENTES:
        try:
            response = session.get(source["url"], timeout=25)
            if response.status_code >= 400:
                print(f"Oficial/{source['tienda']}: HTTP {response.status_code}")
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            seen = set()
            encontrados = _from_json_ld(soup, source, seen)
            encontrados.extend(_from_links(soup, source, seen))
            resultados.extend(encontrados[:120])
            print(f"Oficial/{source['tienda']}: {len(encontrados[:120])} productos")
        except requests.RequestException as error:
            print(f"Oficial/{source['tienda']}: {error}")
        except Exception as error:
            print(f"Oficial/{source['tienda']}: error {error}")
        time.sleep(0.5)

    return resultados
