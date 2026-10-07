import html
import os
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.liquidation_engine import detect_priority_brand, infer_category
from core.ai_reparador import reparar_url

BUSQUEDAS = [
    "celular smartphone", "iphone", "laptop", "computacion", "tablet", "monitor", "impresora",
    "smart tv", "pantalla oled", "streaming", "playstation", "xbox", "nintendo switch",
    "audifonos", "bocinas", "camara", "smart home", "lavadora", "refrigerador", "microondas",
    "aire acondicionado", "colchon", "muebles", "cocina", "herramientas", "jardineria",
    "ropa", "calzado", "tenis", "bolsas", "deportes fitness", "belleza", "maquillaje",
    "skincare", "perfumes", "bebe", "pañales", "carriola", "juguetes", "videojuegos",
    "libros", "mascotas", "automotriz", "llantas", "papeleria oficina", "despensa",
    "limpieza", "farmacia bienestar", "viajes equipaje", "bicicleta scooter",
    "electrodomesticos pequeños", "temporada fiestas", "liquidacion", "remate", "outlet"
]
TIENDAS = {
    "Walmart MX": "https://www.walmart.com.mx/search?q={q}",
    "Bodega Aurrera": "https://www.bodegaaurrera.com.mx/search?q={q}",
    "Chedraui": "https://www.chedraui.com.mx/search?q={q}",
    "Mercado Libre MX": "https://listado.mercadolibre.com.mx/{q}",
    "Soriana": "https://www.soriana.com/buscar?q={q}",
    "Liverpool": "https://www.liverpool.com.mx/tienda?s={q}",
    "Amazon MX": "https://www.amazon.com.mx/s?k={q}",
    "Coppel": "https://www.coppel.com/ofertas",
    "Suburbia": "https://www.suburbia.com.mx/tienda/ofertas/catst62289453",
    "Oferstock": "https://www.oferstock.com.mx/",
}

KEYWORDS_LIQUIDACION = (
    "liquidacion", "liquidación", "remate", "outlet", "ultima pieza",
    "última pieza", "ultimas piezas", "últimas piezas", "saldo", "saldos",
    "caja abierta", "open box", "precio especial", "clearance", "warehouse"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Cache-Control": "no-cache",
}

def normalizar_texto(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


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

def extraer_precios(texto):
    encontrados = re.findall(
        r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)",
        texto or "",
    )
    salida = []
    for valor in encontrados:
        try:
            numero = float(valor.replace(",", ""))
            if 1 <= numero <= 2_000_000:
                salida.append(numero)
        except ValueError:
            pass
    return salida

def calcular_descuento(anterior, actual):
    if not anterior or not actual or anterior <= actual:
        return 0
    return round((1 - actual / anterior) * 100)

def producto_id(tienda, titulo, url):
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if "amazon.com.mx" in host:
        match = re.search(r"/dp/([A-Z0-9]{10})", path, re.I)
        canonical = f"/dp/{match.group(1).upper()}" if match else path
    else:
        canonical = path
    return hashlib.sha256(f"{tienda}|{canonical or titulo}".encode("utf-8")).hexdigest()

def es_url_producto(url, base):
    """Acepta únicamente enlaces que parezcan apuntar al producto, no al buscador/tienda."""
    if not url:
        return False
    parsed = urlparse(url)
    base_host = urlparse(base).netloc.lower()
    host = parsed.netloc.lower()
    if not host or (host != base_host and not (base_host.endswith("bodegaaurrera.com.mx") and host.endswith("bodegaaurrera.com.mx"))):
        return False
    path = parsed.path.lower().rstrip("/")
    full = url.lower()
    if not path or path in ("", "/"):
        return False
    bloqueadas = ("/search", "/buscar", "/ofertas", "/oferta", "/marcas", "/catalogo", "/catalog", "/home", "/hot-sale", "/tienda?s=", "/listado/")
    if any(x in full for x in bloqueadas):
        return False
    # Patrones conocidos de páginas de producto.
    patrones = (
        ("amazon.com.mx", ("/dp/", "/gp/product/")),
        ("mercadolibre.com.mx", ("/mlm-", "-p-")),
        ("walmart.com.mx", ("/ip/", "/p/")),
        ("bodegaaurrera.com.mx", ("/ip/", "/p/")),
        ("despensa.bodegaaurrera.com.mx", ("/ip/", "/p/", "/browse/")),
        ("chedraui.com.mx", ("/p/", "/p")),
        ("liverpool.com.mx", ("/pdp/", "/producto/", "/p/")),
        ("coppel.com", ("/p/", "/producto/")),
        ("suburbia.com.mx", ("/producto/", "/p/", "/tienda/p/", "/tienda/pdp/")),
        ("soriana.com", ("/producto/", "/p/")),
    )
    for dominio, rutas in patrones:
        if dominio in host:
            return any(r in path for r in rutas) or bool(re.search(r"/\d{5,}(?:/)?$", path))
    # Oferstock y sitios no normalizados: exigir señales de producto.
    if "oferstock.com.mx" in host:
        return any(x in full for x in ("/producto", "/product", "/item", "/p/")) and len(path) > 8
    return len(path) > 8

def preparar_url_producto(url, tienda, contexto, base_url):
    """Repara el enlace y exige que siga siendo un enlace directo de producto."""
    reparada = reparar_url(url, tienda, contexto)
    return reparada if es_url_producto(reparada, base_url) else ""

def recorrer_json(obj):
    if isinstance(obj, dict):
        yield obj
        for valor in obj.values():
            yield from recorrer_json(valor)
    elif isinstance(obj, list):
        for valor in obj:
            yield from recorrer_json(valor)

def valor_brand(brand):
    if isinstance(brand, dict):
        return brand.get("name") or ""
    return brand or ""

def valor_category(value):
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""

def extraer_imagen_producto(soup, nodo=None, base_url=""):
    candidatos = []
    if nodo and isinstance(nodo, dict):
        imagen = nodo.get("image")
        if isinstance(imagen, list): candidatos.extend(imagen)
        elif imagen: candidatos.append(imagen)
    for selector in ("meta[property='og:image']", "meta[name='twitter:image']", "link[rel='image_src']"):
        el = soup.select_one(selector)
        if el: candidatos.append(el.get("content") or el.get("href"))
    if soup:
        img = soup.select_one("img[src], img[data-src], img[data-lazy-src]")
        if img: candidatos.append(img.get("src") or img.get("data-src") or img.get("data-lazy-src"))
    for value in candidatos:
        if isinstance(value, dict): value = value.get("url")
        if value:
            value = urljoin(base_url, str(value).strip())
            if value.startswith(("http://", "https://")): return value
    return ""


def extraer_json_ld(soup, tienda, base_url, liquidacion_contexto=False):
    resultados = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        for obj in recorrer_json(data):
            if str(obj.get("@type", "")).lower() != "product":
                continue
            titulo = normalizar_texto(obj.get("name", ""))
            if not titulo:
                continue
            url = obj.get("url", "")
            url = urljoin(base_url, url) if url else ""
            url = preparar_url_producto(url, tienda, titulo, base_url)
            offers = obj.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                offers = {}
            actual = offers.get("price") or offers.get("lowPrice")
            anterior = None
            price_spec = offers.get("priceSpecification")
            if isinstance(price_spec, dict):
                anterior = price_spec.get("priceBeforeDiscount") or price_spec.get("listPrice")
            try:
                actual = float(str(actual).replace(",", "")) if actual else None
                anterior = float(str(anterior).replace(",", "")) if anterior else None
            except (ValueError, TypeError):
                actual = anterior = None
            if not actual or not es_url_producto(url, base_url):
                continue
            marca = normalizar_texto(valor_brand(obj.get("brand")))
            categoria = normalizar_texto(valor_category(obj.get("category")))
            texto_contexto = f"{titulo} {marca} {categoria}"
            es_liq = liquidacion_contexto or any(k in texto_contexto.lower() for k in KEYWORDS_LIQUIDACION)
            marca_detectada, _ = detect_priority_brand({"titulo": titulo, "marca": marca})
            categoria_detectada = infer_category({"titulo": titulo, "marca": marca, "categoria": categoria})
            resultados.append({
                "tienda": tienda,
                "titulo": limpiar_titulo_producto(titulo, url),
                "marca": marca_detectada or marca,
                "categoria": categoria_detectada or categoria,
                "precio_actual": actual,
                "precio_anterior": anterior if anterior and anterior > actual else None,
                "descuento": calcular_descuento(anterior, actual) if anterior else 0,
                "url": url,
                "imagen": extraer_imagen_producto(soup, obj, base_url),
                "liquidacion": es_liq,
                "outlet": any(k in texto_contexto.lower() for k in ("outlet", "clearance", "open box", "warehouse")),
            })
    return resultados

def extraer_tarjetas(soup, tienda, base_url, liquidacion_contexto=False):
    resultados = []
    selectores = [
        "article", "[data-testid*='product']", "[data-testid*='Product']",
        "[class*='product-card']", "[class*='ProductCard']",
        "[class*='product-tile']", "[class*='ProductTile']", "li[class*='product']"
    ]
    vistos_nodos = set()
    for selector in selectores:
        for nodo in soup.select(selector)[:250]:
            identidad = id(nodo)
            if identidad in vistos_nodos:
                continue
            vistos_nodos.add(identidad)
            texto = normalizar_texto(nodo.get_text(" ", strip=True))
            if len(texto) < 15 or len(texto) > 1200:
                continue
            precios = extraer_precios(texto)
            if not precios:
                continue
            enlace = nodo.find("a", href=True)
            url = urljoin(base_url, enlace["href"]) if enlace else base_url
            titulo_node = nodo.find(["h2","h3","h4"])
            titulo = normalizar_texto(titulo_node.get_text(" ", strip=True) if titulo_node else "")
            if not titulo and enlace:
                titulo = normalizar_texto(enlace.get_text(" ", strip=True))
            if not titulo:
                titulo = texto[:180]
            marca_attr = nodo.select_one("[itemprop='brand'], [data-brand], [class*='brand']")
            marca = normalizar_texto(marca_attr.get("content") if marca_attr and marca_attr.get("content") else marca_attr.get_text(" ", strip=True) if marca_attr else "")
            categoria_attr = nodo.select_one("[itemprop='category'], [data-category], [class*='category']")
            categoria = normalizar_texto(categoria_attr.get("content") if categoria_attr and categoria_attr.get("content") else categoria_attr.get_text(" ", strip=True) if categoria_attr else "")
            # Walmart y otras tiendas pueden repetir el precio en formato
            # "$X precio actual $X, Antes $Y ... Ahorra $Z". Las etiquetas
            # tienen prioridad sobre la posición del precio para no confundir
            # "Ahorra" con el precio actual.
            def precio_etiquetado(patterns):
                for pattern in patterns:
                    match = re.search(pattern, texto, flags=re.I)
                    if match:
                        try:
                            return float(match.group(1).replace(",", ""))
                        except (TypeError, ValueError):
                            pass
                return None

            actual = precio_etiquetado([
                r"precio\s+(?:actual|final|de\s+oferta)\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
                r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?=precio\s+actual)",
                r"\bahora\s*:?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
            ])
            anterior = precio_etiquetado([
                r"(?:antes|precio\s+(?:anterior|regular)|original)\s*:?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
            ])
            if actual is None:
                # Fallback conservador: el primer precio, excluyendo importes
                # claramente asociados a "Ahorra" o financiamiento.
                precios_utiles = [
                    p for p in precios
                    if not re.search(rf"ahorra\s+\$?\s*{re.escape(f'{p:g}')}", texto, flags=re.I)
                ]
                actual = precios_utiles[0] if precios_utiles else precios[0]
            if anterior is None:
                anterior = next((p for p in precios if p > actual), None)
            dcto = calcular_descuento(anterior, actual)
            es_liq = liquidacion_contexto or any(k in texto.lower() for k in KEYWORDS_LIQUIDACION)
            if not anterior and not es_liq:
                continue
            url = preparar_url_producto(url, tienda, texto, base_url)
            if not url:
                continue
            marca_detectada, _ = detect_priority_brand({"titulo": titulo, "marca": marca})
            categoria_detectada = infer_category({"titulo": titulo, "marca": marca, "categoria": categoria})
            resultados.append({
                "tienda": tienda,
                "titulo": titulo[:180],
                "marca": marca_detectada or marca,
                "categoria": categoria_detectada or categoria,
                "precio_actual": actual,
                "precio_anterior": anterior,
                "descuento": dcto,
                "url": url,
                "imagen": extraer_imagen_producto(soup, None, base_url),
                "liquidacion": es_liq,
                "outlet": any(k in texto.lower() for k in ("outlet", "clearance", "open box", "warehouse")),
            })
    return resultados


GOOGLE_HEADERS = {
    **HEADERS,
    "Referer": "https://www.google.com/",
}

SORiana_GOOGLE_QUERIES = [
    "site:soriana.com pantallas oferta",
    "site:soriana.com celulares oferta",
    "site:soriana.com laptop oferta",
    "site:soriana.com consola oferta",
    "site:soriana.com videojuegos oferta",
    "site:soriana.com audio oferta",
    "site:soriana.com lavadora oferta",
    "site:soriana.com refrigerador oferta",
    "site:soriana.com microondas oferta",
    "site:soriana.com juguetes oferta",
    "site:soriana.com belleza oferta",
    "site:soriana.com bebe oferta",
    "site:soriana.com hogar oferta",
    "site:soriana.com liquidacion",
    "site:soriana.com remate",
]

def extraer_url_soriana_google(href):
    if not href:
        return ""
    href = href.strip()
    if href.startswith("/url?q="):
        href = href.split("/url?q=", 1)[1].split("&", 1)[0]
    href = urljoin("https://www.google.com", href)
    parsed = urlparse(href)
    if parsed.netloc.lower() not in ("www.soriana.com", "soriana.com"):
        return ""
    if any(x in parsed.path.lower() for x in ("/buscar", "/marcas/", "/marcas-propias", "/despensa/", "/especiales/", "/ofertas/")):
        return ""
    return href

def buscar_soriana_desde_google(session):
    """Fallback para obtener enlaces públicos indexados cuando Soriana bloquea al runner."""
    resultados = []
    vistos = set()

    for consulta in SORiana_GOOGLE_QUERIES:
        try:
            response = session.get(
                "https://www.google.com/search",
                params={"q": consulta, "hl": "es", "gl": "mx", "num": 10},
                headers=GOOGLE_HEADERS,
                timeout=20,
            )
            if response.status_code >= 400:
                print(f"Soriana/Google: HTTP {response.status_code} para {consulta}")
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            for enlace in soup.select("a[href]"):
                url = extraer_url_soriana_google(enlace.get("href"))
                if not url or url in vistos:
                    continue

                texto = normalizar_texto(enlace.get_text(" ", strip=True))
                if len(texto) < 8:
                    continue

                padre = enlace.find_parent()
                contexto = normalizar_texto(padre.get_text(" ", strip=True) if padre else texto)
                precios = extraer_precios(contexto)
                actual = precios[0] if precios else None
                anterior = next((p for p in precios[1:] if p > actual), None) if actual else None

                titulo = texto[:180]
                if titulo.lower() in ("soriana", "comprar en soriana", "soriana en línea"):
                    continue

                marca, _ = detect_priority_brand({"titulo": titulo})
                categoria = infer_category({"titulo": titulo})
                contexto_lower = contexto.lower()
                liquidacion = any(k in contexto_lower for k in KEYWORDS_LIQUIDACION)

                resultados.append({
                    "tienda": "Soriana",
                    "titulo": titulo,
                    "marca": marca,
                    "categoria": categoria,
                    "precio_actual": actual or 0,
                    "precio_anterior": anterior,
                    "descuento": calcular_descuento(anterior, actual) if actual and anterior else 0,
                    "url": url,
                    "liquidacion": liquidacion,
                    "outlet": any(k in contexto_lower for k in ("outlet", "clearance", "open box", "warehouse")),
                    "origen_link": "Google",
                })
                vistos.add(url)

        except requests.RequestException as error:
            print(f"Soriana/Google: error de red para {consulta}: {error}")
        except Exception as error:
            print(f"Soriana/Google: error procesando {consulta}: {error}")

        time.sleep(0.4)

    print(f"Soriana/Google: {len(resultados)} enlaces públicos indexados encontrados")
    return resultados

def buscar_soriana(session):
    """Consulta Soriana directamente con pocas búsquedas de alto valor.
    Si Soriana responde 403/429, se corta la fuente en este ciclo en lugar de
    bombardear Google y generar una cascada de errores.
    """
    resultados = []
    consultas = [
        "ofertas", "pantallas", "celulares", "videojuegos", "hogar", "belleza", "mascotas"
    ]
    vistos = {}
    for consulta in consultas:
        url = "https://www.soriana.com/buscar?q=" + quote_plus(consulta)
        try:
            response = session.get(url, timeout=25)
            if response.status_code in (403, 429):
                print(f"Soriana: HTTP {response.status_code} para '{consulta}'; fuente pausada hasta el siguiente ciclo.")
                break
            if response.status_code == 404:
                print(f"Soriana: ruta de búsqueda no encontrada para '{consulta}'; se omite sin reintento.")
                continue
            if response.status_code >= 400:
                print(f"Soriana: HTTP {response.status_code} para '{consulta}'")
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            candidatos = extraer_json_ld(soup, "Soriana", url, True)
            candidatos.extend(extraer_tarjetas(soup, "Soriana", url, True))
            for item in candidatos:
                actual = item.get("precio_actual") or 0
                item_url = item.get("url") or ""
                if actual <= 0 or not item_url:
                    continue
                clave = producto_id("Soriana", item.get("titulo", ""), item_url)
                existente = vistos.get(clave)
                if not existente or item.get("descuento", 0) > existente.get("descuento", 0):
                    item["id"] = clave
                    vistos[clave] = item
            print(f"Soriana: {consulta} -> {len(candidatos)} candidatos parseados")
            time.sleep(0.8)
        except requests.RequestException as error:
            print(f"Soriana: error de red en '{consulta}': {error}; fuente pausada.")
            break
        except Exception as error:
            print(f"Soriana: error procesando '{consulta}': {error}")
    resultados = list(vistos.values())
    publicables = [x for x in resultados if (x.get("descuento") or 0) >= 50]
    if not resultados or not publicables:
        print("Soriana: sin candidatos publicables >=50%; activando fallback de enlaces públicos indexados.")
        fallback = buscar_soriana_desde_google(session)
        resultados.extend(fallback)
    print(f"Soriana: {len(resultados)} productos/enlaces candidatos")
    return resultados

def _urls_busqueda(nombre, q, plantilla):
    """Devuelve URLs de búsqueda válidas y conservadoras por tienda.
    Chedraui cambió su estructura de búsqueda; para jardinería usamos la
    categoría pública vigente en lugar del endpoint /search que devuelve 404.
    """
    primaria = plantilla.format(q=quote_plus(q))
    if nombre == "Mercado Libre MX":
        return [
            primaria,
            "https://www.mercadolibre.com.mx/ofertas",
        ]
    if nombre == "Amazon MX":
        return [
            primaria,
            "https://www.amazon.com.mx/deals",
            "https://www.amazon.com.mx/gp/goldbox",
        ]
    if nombre != "Chedraui":
        return [primaria]

    normalizada = q.lower().strip().replace("í", "i").replace("á", "a")
    if normalizada in {
        "jardineria",
        "jardin",
        "herramientas de jardineria",
        "plantas",
        "plantas y flores",
        "macetas",
    }:
        return [
            "https://www.chedraui.com.mx/hogar-y-jardin/patio-y-jardin",
            primaria,
        ]
    return [primaria]


EXTREME_QUERIES = [
    "liquidacion 99", "liquidacion 95", "99% descuento", "95% descuento",
    "precio extremo", "precio 1", "precio 10", "remate 99", "remate 95",
    "ultima pieza", "ultimas piezas", "clearance"
]

# No se limita la cacería a 95/99: estos rangos son los que el motor puede
# confirmar con precio actual + referencia. Las consultas sirven para descubrir
# páginas públicas; el porcentaje final siempre se calcula con los precios.
DESCUENTO_QUERIES = [
    "oferta 50%", "oferta 60%", "oferta 70%", "oferta 80%", "oferta 90%",
    "descuento 50%", "descuento 60%", "descuento 70%", "descuento 80%", "descuento 90%",
    "rebaja", "oferta", "promocion", "cupon", "liquidacion", "remate",
]


def verificar_pagina_producto(url, tienda, session):
    """Verifica una PDP y prioriza el producto de la propia ficha.
    
    Una PDP puede contener tarjetas de productos relacionados. Nunca debemos
    elegir como producto principal el relacionado sólo porque tenga un mayor
    descuento. JSON-LD con URL/título coincidente tiene prioridad; las tarjetas
    sólo son un último recurso.
    """
    try:
        response = session.get(url, timeout=25, allow_redirects=True)
        if response.status_code >= 400:
            return None

        final_url = response.url
        soup = BeautifulSoup(response.text, "html.parser")
        target_path = urlparse(final_url).path.rstrip("/").lower()
        target_tokens = set(re.findall(r"[a-z0-9]{4,}", target_path))

        json_rows = extraer_json_ld(soup, tienda, final_url, False)
        card_rows = extraer_tarjetas(soup, tienda, final_url, False)

        def score(row):
            row_url = str(row.get("url") or "")
            row_path = urlparse(row_url).path.rstrip("/").lower()
            same_url = row_path == target_path
            title_tokens = set(re.findall(
                r"[a-z0-9]{4,}",
                normalizar_texto(row.get("titulo", "")).lower(),
            ))
            overlap = len(target_tokens & title_tokens)
            return (
                1000 if same_url else 0,
                min(overlap, 20),
                20 if row.get("precio_anterior") else 0,
                float(row.get("descuento") or 0),
            )

        rows = json_rows or []
        if not rows:
            rows = card_rows
        if not rows:
            return None

        row = max(rows, key=score)
        row["url"] = final_url
        row["origen_link"] = "ficha_directa_verificada"
        row["verificado_en_tienda"] = True
        row["imagen"] = row.get("imagen") or extraer_imagen_producto(soup, None, final_url)
        return row
    except requests.RequestException as error:
        print(f"{tienda}: error verificando ficha {url}: {error}")
        return None
    except Exception as error:
        print(f"{tienda}: error procesando ficha {url}: {error}")
        return None


def _buscar_indexado_tienda(nombre, consultas, session):
    """Descubre productos desde resultados públicos indexados cuando la tienda
    devuelve HTTP 200 pero oculta el catálogo al cliente automatizado.
    No intenta saltar CAPTCHA/WAF/autenticación.
    """
    dominio = {
        "Walmart MX": "walmart.com.mx",
        "Bodega Aurrera": "bodegaaurrera.com.mx",
        "Amazon MX": "amazon.com.mx",
        "Mercado Libre MX": "mercadolibre.com.mx",
    }.get(nombre)
    if not dominio:
        return []

    resultados = []
    vistos = set()
    motores = [
        ("Bing", "https://www.bing.com/search", {"setlang": "es-MX", "cc": "mx", "count": 10}),
        ("Google", "https://www.google.com/search", {"hl": "es", "gl": "mx", "num": 10}),
    ]
    for consulta in consultas:
        q = f"site:{dominio} {consulta} -search -buscar -login"
        soup = None
        motor_usado = ""
        for motor, endpoint, extra_params in motores:
            try:
                response = session.get(endpoint, params={"q": q, **extra_params},
                                       headers=GOOGLE_HEADERS, timeout=20)
                if response.status_code >= 400:
                    print(f"{nombre}/{motor}: HTTP {response.status_code} para {consulta}")
                    continue
                soup = BeautifulSoup(response.text, "html.parser")
                motor_usado = motor
                break
            except requests.RequestException as error:
                print(f"{nombre}/{motor}: error de red para {consulta}: {error}")

        if soup is None:
            continue

        try:
            for enlace in soup.select("a[href]"):
                href = enlace.get("href", "")
                if href.startswith("/url?q="):
                    href = href.split("/url?q=", 1)[1].split("&", 1)[0]
                parsed = urlparse(href)
                if parsed.scheme not in ("http", "https"):
                    continue
                host = parsed.netloc.lower()
                if not (host == dominio or host.endswith("." + dominio) or (nombre == "Bodega Aurrera" and host.endswith("bodegaaurrera.com.mx"))):
                    continue
                base_host = "https://www.bodegaaurrera.com.mx/" if nombre == "Bodega Aurrera" else f"https://www.{dominio}/"
                if not es_url_producto(href, base_host):
                    continue

                texto = normalizar_texto(enlace.get_text(" ", strip=True))
                padre = enlace.find_parent()
                contexto = normalizar_texto(padre.get_text(" ", strip=True) if padre else texto)
                precios = extraer_precios(contexto)
                actual = precios[0] if precios else None
                anterior = next((p for p in precios[1:] if p > actual), None) if actual else None
                if not actual:
                    verificado = verificar_pagina_producto(href, nombre, session)
                    if verificado:
                        clave = producto_id(nombre, verificado.get("titulo", ""), verificado.get("url", href))
                        if clave not in vistos:
                            vistos.add(clave)
                            resultados.append(verificado)
                    continue

                clave = producto_id(nombre, texto, href)
                if clave in vistos:
                    continue
                vistos.add(clave)
                marca, _ = detect_priority_brand({"titulo": texto})
                categoria = infer_category({"titulo": texto})
                contexto_lower = contexto.lower()
                resultados.append({
                    "tienda": nombre,
                    "titulo": limpiar_titulo_producto(texto, href),
                    "marca": marca,
                    "categoria": categoria,
                    "precio_actual": actual,
                    "precio_anterior": anterior,
                    "descuento": calcular_descuento(anterior, actual),
                    "url": href,
                    "liquidacion": any(k in contexto_lower for k in KEYWORDS_LIQUIDACION),
                    "outlet": any(k in contexto_lower for k in ("outlet", "clearance", "open box", "warehouse")),
                    "origen_link": "buscador_publico",
                    "motor_descubrimiento": motor_usado,
                })
        except Exception as error:
            print(f"{nombre}/Indexado: error procesando {consulta}: {error}")

        time.sleep(0.4)

    print(f"{nombre}/Indexado: {len(resultados)} productos públicos descubiertos")
    return resultados
def buscar_tienda(nombre, plantilla, session):
    resultados = []
    urls_consultadas = set()
    base_url = plantilla.split("{q}", 1)[0]
    # Rotación de consultas: cubre todas las categorías a lo largo de los ciclos
    # sin lanzar cientos de peticiones por cada ejecución de 15 minutos.
    import time as _time
    bloque = max(1, int(_time.time() // 900))
    tam = 12
    inicio = (bloque * tam) % len(BUSQUEDAS)
    consultas_base = [BUSQUEDAS[(inicio + i) % len(BUSQUEDAS)] for i in range(tam)]
    consultas = []
    # No monopolizar el ciclo con 95/99: mezclamos extremos con rangos medios.
    for q in (EXTREME_QUERIES[:4] + DESCUENTO_QUERIES[:6]):
        if q not in consultas and len(consultas) < tam:
            consultas.append(q)
    for q in consultas_base:
        if q not in consultas and len(consultas) < tam:
            consultas.append(q)
    for prioritaria in ("liquidacion", "remate", "outlet"):
        if prioritaria in BUSQUEDAS and prioritaria not in consultas and len(consultas) < tam:
            consultas.append(prioritaria)
    for q in consultas:
        urls_busqueda = _urls_busqueda(nombre, q, plantilla)
        response = None
        url = urls_busqueda[0]
        try:
            for indice_url, candidata_url in enumerate(urls_busqueda):
                if candidata_url in urls_consultadas:
                    continue
                urls_consultadas.add(candidata_url)
                url = candidata_url
                response = None
                for intento in range(3):
                    response = session.get(url, timeout=20)
                    if response.status_code == 404:
                        if indice_url + 1 < len(urls_busqueda):
                            print(f"{nombre}: HTTP 404 para {q}; usando ruta alternativa oficial.")
                        else:
                            print(f"{nombre}: HTTP 404 para {q}; se omite esa búsqueda.")
                        break
                    if response.status_code == 429 or response.status_code >= 500:
                        espera = 2 ** intento
                        print(f"{nombre}: HTTP {response.status_code} para {q}; reintento en {espera}s.")
                        if intento < 2:
                            time.sleep(espera)
                            continue
                    break
                if response is not None and response.status_code < 400:
                    break

            if response is None or response.status_code >= 400:
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            liquidacion_contexto = any(k in q.lower() for k in KEYWORDS_LIQUIDACION)
            candidatos = extraer_json_ld(soup, nombre, url, liquidacion_contexto)
            candidatos.extend(extraer_tarjetas(soup, nombre, url, liquidacion_contexto))
            unicos = {}
            for item in candidatos:
                actual = item.get("precio_actual") or 0
                if actual <= 0:
                    continue
                clave = producto_id(nombre, item["titulo"], item["url"])
                existente = unicos.get(clave)
                if not existente or item.get("descuento", 0) > existente.get("descuento", 0):
                    item["id"] = clave
                    unicos[clave] = item
            resultados.extend(unicos.values())
            print(f"{nombre}: {q} -> {len(unicos)} productos candidatos")
            time.sleep(0.5)
        except requests.RequestException as error:
            print(f"{nombre}: error de red para {q}: {error}")
        except Exception as error:
            print(f"{nombre}: error procesando {q}: {error}")
    # Walmart/Bodega pueden entregar HTTP 200 con catálogo normal pero ocultar
    # las liquidaciones. En ese caso el índice público funciona como segunda
    # fuente, no sólo cuando el HTML queda completamente vacío.
    if nombre in ("Walmart MX", "Bodega Aurrera", "Amazon MX", "Mercado Libre MX"):
        comparables = [x for x in resultados if (x.get("descuento") or 0) >= 50]
        if not resultados or not comparables:
            fallback_queries = DESCUENTO_QUERIES[:10] + EXTREME_QUERIES
            resultados.extend(_buscar_indexado_tienda(nombre, fallback_queries, session))
    return resultados


def _agregar_candidatos(resultados, candidatos, tienda):
    unicos = {}
    for item in candidatos:
        actual = item.get("precio_actual") or 0
        if actual <= 0:
            continue
        url = item.get("url") or ""
        if not url or not es_url_producto(url, item.get("_base_url", url)):
            continue
        clave = producto_id(tienda, item.get("titulo", ""), url)
        existente = unicos.get(clave)
        if not existente or item.get("descuento", 0) > existente.get("descuento", 0):
            item["id"] = clave
            item.pop("_base_url", None)
            unicos[clave] = item
    resultados.extend(unicos.values())


def buscar_urls_oficiales(tienda, urls, session, forzar_liquidacion=True):
    """Extrae productos de páginas públicas oficiales de una tienda."""
    resultados = []
    for url in urls:
        try:
            response = session.get(url, timeout=25)
            if response.status_code >= 400:
                print(f"{tienda}: HTTP {response.status_code} en {url}")
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            candidatos = extraer_json_ld(soup, tienda, url, forzar_liquidacion)
            candidatos.extend(extraer_tarjetas(soup, tienda, url, forzar_liquidacion))
            for item in candidatos:
                item["_base_url"] = url
            _agregar_candidatos(resultados, candidatos, tienda)
            print(f"{tienda}: {url} -> {len(candidatos)} candidatos")
        except requests.RequestException as error:
            print(f"{tienda}: error de red en {url}: {error}")
        except Exception as error:
            print(f"{tienda}: error procesando {url}: {error}")
        time.sleep(0.5)
    return resultados


def buscar_coppel(session):
    urls = [
        "https://www.coppel.com/ca/outlet-saldos",
        "https://www.coppel.com/ofertas",
        "https://www.coppel.com/l/ofertas",
        "https://www.coppel.com/l/almacen-de-ofertas/",
        "https://www.coppel.com/l/rebajas-verano",
    ]
    return buscar_urls_oficiales("Coppel", urls, session, True)


def buscar_suburbia(session):
    urls = [
        "https://www.suburbia.com.mx/",
        "https://www.suburbia.com.mx/tienda/home",
        "https://www.suburbia.com.mx/tienda/pdp/Top/SB5014091999",
    ]
    return buscar_urls_oficiales("Suburbia", urls, session, True)


def _url_interna_oferstock(url):
    parsed = urlparse(url)
    if parsed.netloc.lower() not in ("oferstock.com.mx", "www.oferstock.com.mx"):
        return False
    path = parsed.path.lower()
    if path in ("", "/"):
        return False
    if any(x in path for x in ("/contact", "/contacto", "/aviso", "/privacidad", "/terminos", "/login", "/mi-cuenta", "/carrito")):
        return False
    return True


# Tiendas físicas / remates de referencia en el radio solicitado.
# Se usan como fuentes de oportunidades y ubicaciones; no se inventan precios
# ni se publican como oferta hasta obtener un producto/precio verificable.
TIENDAS_FISICAS = [
    {"nombre":"Coppel Remate Los Reyes","zona":"Los Reyes La Paz","tipo":"remate","direccion":"Carretera Federal México-Puebla Km 17.5, Los Reyes La Paz, Edomex"},
    {"nombre":"Bodega De Remates Los Pepos","zona":"Santa Cruz Meyehualco, Iztapalapa","tipo":"remate","direccion":"Justo Sierra 43, Santa Cruz Meyehualco, Iztapalapa, CDMX"},
    {"nombre":"Coppel Fernando Arruti","zona":"Santa Martha Acatitla Norte, Iztapalapa","tipo":"saldos","direccion":"Calz. Ignacio Zaragoza 2514, Santa Martha Acatitla Norte, Iztapalapa, CDMX"},
    {"nombre":"Liverpool Ciudad Jardín","zona":"Ciudad Nezahualcóyotl","tipo":"departamental","direccion":"Av. Bordo de Xochiaca 3, Plaza Ciudad Jardín, Cd. Nezahualcóyotl, Edomex"},
    {"nombre":"Liverpool Parque Tezontle","zona":"Iztapalapa","tipo":"departamental","direccion":"Av. Canal de Tezontle 851, Iztapalapa, CDMX"},
    {"nombre":"Coppel Santa Martha","zona":"Santa Martha Acatitla, Iztapalapa","tipo":"departamental","direccion":"Av. Ermita Iztapalapa 4170, Iztapalapa, CDMX"},
    {"nombre":"Walmart Plaza Oriente","zona":"Iztapalapa","tipo":"supermercado","direccion":"Canal de Tezontle, junto a Parque Tezontle, CDMX"},
    {"nombre":"Bodega Aurrera El Salado","zona":"El Salado, Iztapalapa","tipo":"remate","direccion":"Zona El Salado, Iztapalapa, CDMX"},
    {"nombre":"Soriana Híper Plaza Sendero Ixtapaluca","zona":"Ixtapaluca","tipo":"supermercado","direccion":"Plaza Sendero Ixtapaluca, Estado de México"},
]

def buscar_oferstock(session):
    """
    Oferstock: rastreo ligero de la portada y de enlaces internos relacionados
    con ofertas/liquidación/outlet/stock. No presupone un CMS concreto.
    """
    raiz = "https://www.oferstock.com.mx/"
    resultados = []
    pendientes = [raiz]
    visitadas = set()

    while pendientes and len(visitadas) < 12:
        url = pendientes.pop(0)
        if url in visitadas:
            continue
        visitadas.add(url)
        try:
            response = session.get(url, timeout=25)
            if response.status_code >= 400:
                print(f"Oferstock: HTTP {response.status_code} en {url}")
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            candidatos = extraer_json_ld(soup, "Oferstock", url, True)
            candidatos.extend(extraer_tarjetas(soup, "Oferstock", url, True))
            for item in candidatos:
                item["_base_url"] = url
                item["origen_link"] = "Oferstock"
            _agregar_candidatos(resultados, candidatos, "Oferstock")

            for enlace in soup.select("a[href]"):
                href = urljoin(url, enlace.get("href", ""))
                if not _url_interna_oferstock(href) or href in visitadas or href in pendientes:
                    continue
                texto = normalizar_texto(enlace.get_text(" ", strip=True)).lower()
                path = urlparse(href).path.lower()
                if any(k in (texto + " " + path) for k in (
                    "oferta", "ofertas", "liquid", "remate", "outlet", "stock",
                    "producto", "productos", "tienda", "catalogo", "categor"
                )):
                    pendientes.append(href)

        except requests.RequestException as error:
            print(f"Oferstock: error de red en {url}: {error}")
        except Exception as error:
            print(f"Oferstock: error procesando {url}: {error}")
        time.sleep(0.4)

    print(f"Oferstock: {len(resultados)} productos candidatos encontrados")
    return resultados


def buscar_todas():
    """Descubrimiento legacy paralelo y aislado por tienda.

    Cada tienda obtiene su propia Session para evitar compartir estado/cookies
    entre workers. Los adaptadores API-first se ejecutan en otra capa y luego
    se deduplican en el monitor.
    """
    skip = {x.strip() for x in os.environ.get("PREFLIGHT_SKIP_SOURCES", "").split(",") if x.strip()}
    tareas = [(nombre, plantilla) for nombre, plantilla in TIENDAS.items()
              if nombre not in skip]

    def ejecutar(tarea):
        nombre, plantilla = tarea
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            if nombre == "Walmart MX":
                urls = [
                    "https://www.walmart.com.mx/content/especiales/360013_300279",
                    "https://www.walmart.com.mx/search?q=oferta",
                    "https://www.walmart.com.mx/search?q=remate",
                    "https://www.walmart.com.mx/search?q=outlet",
                    "https://www.walmart.com.mx/browse/especiales/ahorra-o-nunca/360013_7719936",
                ]
                rows = buscar_urls_oficiales(nombre, urls, session, True)
                if not rows:
                    rows = _buscar_indexado_tienda(nombre, DESCUENTO_QUERIES[:8] + EXTREME_QUERIES, session)
                print(f"{nombre}: fuente oficial/indexada -> {len(rows)} candidatos")
                return rows
            if nombre == "Bodega Aurrera":
                urls = [
                    "https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-para-tu-hogar/490004_1030001_1030004",
                    "https://www.bodegaaurrera.com.mx/search?q=oferta",
                    "https://www.bodegaaurrera.com.mx/search?q=remate",
                    "https://www.bodegaaurrera.com.mx/search?q=outlet",
                    "https://despensa.bodegaaurrera.com.mx/browse/cupones-y-bonificaciones/rebajas-y-mas/8171461_3848205",
                    "https://despensa.bodegaaurrera.com.mx/browse/promociones-bancarias/hogar-y-electronica/2596858/2421581",
                ]
                rows = buscar_urls_oficiales(nombre, urls, session, True)
                if not rows:
                    rows = _buscar_indexado_tienda(nombre, DESCUENTO_QUERIES[:8] + EXTREME_QUERIES, session)
                print(f"{nombre}: fuente oficial/indexada -> {len(rows)} candidatos")
                return rows
            if nombre in ("Amazon MX", "Mercado Libre MX"):
                rows = buscar_tienda(nombre, plantilla, session)
                print(f"{nombre}: búsqueda directa/publica -> {len(rows)} candidatos")
                return rows
            if nombre == "Chedraui":
                print(f"{nombre}: búsqueda HTML omitida; se prioriza API VTEX.")
                return []
            if nombre == "Soriana":
                return buscar_soriana(session)
            if nombre == "Coppel":
                return buscar_coppel(session)
            if nombre == "Suburbia":
                return buscar_suburbia(session)
            if nombre == "Oferstock":
                return buscar_oferstock(session)
            return buscar_tienda(nombre, plantilla, session)
        except Exception as exc:
            print(f"{nombre}: error aislado en worker -> {type(exc).__name__}: {exc}")
            return []

    salida = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tareas))), thread_name_prefix="legacy-store") as pool:
        futures = {pool.submit(ejecutar, tarea): tarea[0] for tarea in tareas}
        for future in as_completed(futures):
            nombre = futures[future]
            try:
                salida.extend(future.result() or [])
            except Exception as exc:
                print(f"{nombre}: worker falló -> {type(exc).__name__}: {exc}")
    return salida

