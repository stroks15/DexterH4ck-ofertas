"""Fuentes oficiales de liquidación/outlet.

Estas fuentes no sustituyen a los scrapers de tienda. Se usan para encontrar
productos que la propia tienda clasifica como liquidación y después pasan por
el mismo motor de verificación.
"""

import json
import os
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
        "urls": (
            "https://www.walmart.com.mx/content/especiales/360013_300279",
            "https://kiosco.www.walmart.com.mx/shop/liquidaciones-walmart",
        ),
        "url": "https://www.walmart.com.mx/content/especiales/360013_300279",
        "host": "walmart.com.mx",
        "patrones": ("/ip/", "/p/"),
        "max_pages": 4,
    },
    {
        "tienda": "Chedraui",
        "urls": (
            "https://www.chedraui.com.mx/promociones/solo-hoy",
            "https://www.chedraui.com.mx/promociones/chedraui",
            "https://www.chedraui.com.mx/promociones/productos-de-limpieza",
            "https://www.chedraui.com.mx/promociones/perfumeria",
        ),
        "url": "https://www.chedraui.com.mx/promociones/solo-hoy",
        "host": "chedraui.com.mx",
        "patrones": ("/p/",),
        "max_pages": 3,
    },
    {
        "tienda": "Sanborns",
        "url": "https://www.sanborns.com.mx/cat/videojuegos?id=12&percent_off=50+TO+80&order=sanborns_price_asc",
        "host": "sanborns.com.mx",
        "patrones": ("/producto/", "/p/", "/item/"),
    },
    {
        "tienda": "Bodega Aurrera",
        "urls": (
            "https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-para-tu-hogar/490004_1030001_1030004",
            "https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-linea-blanca/490004_1030001_1360040",
            "https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-muebles/490004_1030001_1270007",
            "https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-electrodomesticos/490004_1030001_1360004",
            "https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-destacados/490004_1030001_1030002",
            "https://www.bodegaaurrera.com.mx/content/eventos/remates/490004_1030001",
            "https://despensa.bodegaaurrera.com.mx/content/remates/2715538",
            "https://despensa.bodegaaurrera.com.mx/browse/cupones-y-bonificaciones/rebajas-y-mas/8171461_3848205",
        ),
        "url": "https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-para-tu-hogar/490004_1030001_1030004",
        "host": "bodegaaurrera.com.mx",
        "patrones": ("/ip/",),
        "max_pages": 4,
    },
    {
        "tienda": "Coppel",
        "urls": (
            "https://www.coppel.com/ca/outlet-saldos",
            "https://www.coppel.com/ofertas",
        ),
        "url": "https://www.coppel.com/ca/outlet-saldos",
        "host": "coppel.com",
        "patrones": ("/pdp/", "/p/"),
        "max_pages": 2,
    },
    {
        "tienda": "Liverpool",
        "urls": ("https://www.liverpool.com.mx/tienda?s=ofertas+de+liquidaci%C3%B3n",),
        "url": "https://www.liverpool.com.mx/tienda?s=ofertas+de+liquidaci%C3%B3n",
        "host": "liverpool.com.mx",
        "patrones": ("/tienda/pdp/", "/pdp/"),
        "max_pages": 3,
    },
    {
        "tienda": "Suburbia",
        "urls": (
            "https://www.suburbia.com.mx/tienda?s=promociones",
            "https://www.suburbia.com.mx/tienda?s=rebajas",
        ),
        "url": "https://www.suburbia.com.mx/tienda?s=promociones",
        "host": "suburbia.com.mx",
        "patrones": ("/p/", "/producto/"),
        "max_pages": 3,
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
    allowed_host = host == source["host"] or host.endswith("." + source["host"])
    if source["tienda"] == "Bodega Aurrera":
        allowed_host = allowed_host or host.endswith("despensa.bodegaaurrera.com.mx")
    if not host or not allowed_host:
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



def _labelled_prices(text):
    """Extrae precio actual/referencia usando etiquetas, nunca por simple posición."""
    text = " ".join((text or "").split())
    money = r"(?:\$\s*)?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)"
    actual_patterns = (
        rf"precio\s+(?:actual|final|de\s+oferta)\s*:?\s*{money}",
        rf"ahora\s*:?\s*{money}",
        rf"precio\s+en\s+rojo\s*:?\s*{money}",
    )
    reference_patterns = (
        rf"(?:antes|precio\s+(?:anterior|regular)|precio\s+de\s+lista|original)\s*:?\s*{money}",
    )
    def first(patterns):
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except (TypeError, ValueError):
                    pass
        return None
    actual = first(actual_patterns)
    reference = first(reference_patterns)
    # En listados de Liverpool el formato normal es "$actual $regular".
    # Solo aceptamos esa pareja cuando no hay rangos ni mensualidades.
    if actual is None and "/" not in text and "hasta" not in text.lower():
        prices = _prices(text)
        if len(prices) == 2 and prices[1] > prices[0]:
            actual, reference = prices
    if actual and reference and reference <= actual:
        reference = None
    return actual, reference

def _offer_prices(offers):
    """Extrae precio actual y precio de lista cuando la tienda los publica estructurados."""
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return None, None

    def number(value):
        if isinstance(value, dict):
            value = value.get("price") or value.get("value")
        try:
            result = float(str(value).replace(",", "").replace("$", "").strip())
            return result if 0 < result <= 2_000_000 else None
        except (TypeError, ValueError):
            return None

    current = number(offers.get("price") or offers.get("lowPrice"))
    reference = None
    for key in ("compareAtPrice", "listPrice", "wasPrice", "regularPrice",
                "originalPrice", "previousPrice", "priceBefore"):
        reference = number(offers.get(key))
        if reference and (not current or reference > current):
            break
        reference = None

    specs = offers.get("priceSpecification") or []
    if isinstance(specs, dict):
        specs = [specs]
    for spec in specs if isinstance(specs, list) else []:
        if not isinstance(spec, dict):
            continue
        candidate = number(spec.get("price") or spec.get("priceValue") or spec.get("value"))
        kind = " ".join(str(spec.get(k, "")) for k in ("name", "description", "priceType", "@type")).lower()
        if candidate and current is None and ("sale" in kind or "offer" in kind or "actual" in kind):
            current = candidate
        elif candidate and current and candidate > current and any(
            word in kind for word in ("list", "regular", "original", "strike", "reference", "was")
        ):
            reference = candidate

    if reference is not None and current is not None and reference <= current:
        reference = None
    return current, reference


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
            price, previous = _offer_prices(offers)
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
                "precio_anterior": previous,
                "descuento": round((1 - price / previous) * 100, 2) if previous and previous > price else 0,
                "url": url,
                "liquidacion": True,
                "outlet": True,
                "tipo_fuente": "LIQUIDACION_OFICIAL",
                "origen": source["url"],
                "origen_link": source["url"],
            })
    return results


def _listing_links(soup, source):
    found = []
    base = source["url"]
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor.get("href", ""))
        parsed = urlparse(href)
        host = parsed.netloc.lower()
        if host != source["host"] and not host.endswith("." + source["host"]):
            continue
        if _is_product(href, source):
            continue
        text = " ".join(anchor.get_text(" ", strip=True).lower().split())
        haystack = parsed.path.lower() + " " + text
        if any(x in haystack for x in (
            "page=", "pagina", "siguiente", "remates", "liquidacion",
            "liquidación", "ofertas", "rebajas", "promociones", "outlet"
        )):
            if href not in found:
                found.append(href)
    return found[:20]

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

        actual, previous = _labelled_prices(text)
        if actual is None:
            # Solo aceptamos una pareja inequívoca; nunca tomamos el mínimo
            # de una tarjeta que puede contener mensualidades, variantes o ahorro.
            if len(prices) == 2 and prices[1] > prices[0] and "/" not in text and "hasta" not in text.lower():
                actual, previous = prices
            else:
                continue
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
    """Descubre ofertas desde hubs oficiales y una cantidad limitada de páginas."""
    session = session or requests.Session()
    session.headers.update(HEADERS)
    resultados = []
    skip = {x.strip() for x in os.environ.get("PREFLIGHT_SKIP_SOURCES", "").split(",") if x.strip()}

    for source in FUENTES:
        if source["tienda"] in skip:
            print(f"Oficial/{source['tienda']}: omitida por preflight.")
            continue
        seeds = list(source.get("urls") or (source.get("url"),))
        pendientes = seeds[:]
        visitadas = set()
        limite = int(source.get("max_pages", 2))
        while pendientes and len(visitadas) < limite:
            url = pendientes.pop(0)
            if not url or url in visitadas:
                continue
            visitadas.add(url)
            local_source = dict(source)
            local_source["url"] = url
            try:
                response = session.get(url, timeout=25)
                if response.status_code in (403, 429):
                    print(f"Oficial/{source['tienda']}: HTTP {response.status_code}; fuente pausada este ciclo.")
                    break
                if response.status_code >= 400:
                    print(f"Oficial/{source['tienda']}: HTTP {response.status_code} en {url}")
                    continue
                soup = BeautifulSoup(response.text, "html.parser")
                seen = {item.get("url") for item in resultados if item.get("tienda") == source["tienda"] and item.get("url")}
                encontrados = _from_json_ld(soup, local_source, seen)
                encontrados.extend(_from_links(soup, local_source, seen))
                for item in encontrados:
                    item["origen_link"] = url
                    item["fuente_descubrimiento"] = "oficial_hub"
                resultados.extend(encontrados[:120])
                print(f"Oficial/{source['tienda']}: {url} -> {len(encontrados[:120])} productos")
                for link in _listing_links(soup, local_source):
                    if link not in visitadas and link not in pendientes:
                        pendientes.append(link)
                time.sleep(0.5)
            except requests.RequestException as error:
                print(f"Oficial/{source['tienda']}: error de red en {url}: {error}")
            except Exception as error:
                print(f"Oficial/{source['tienda']}: error procesando {url}: {error}")

    unicos = {}
    for item in resultados:
        key = (item.get("tienda"), item.get("url"))
        if not key[1]:
            continue
        old = unicos.get(key)
        if old is None or item.get("descuento", 0) > old.get("descuento", 0):
            unicos[key] = item
    return list(unicos.values())
