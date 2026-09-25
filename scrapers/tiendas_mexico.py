import hashlib
import json
import re
import time
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BUSQUEDAS = ["iphone","laptop","smart tv","playstation","xbox","nintendo switch","audifonos","smartwatch","tenis","refrigerador","lavadora","pantalla","celular","liquidacion","liquidación","remate","outlet"]

TIENDAS = {
    "Walmart MX": "https://www.walmart.com.mx/search?q={q}",
    "Bodega Aurrera": "https://www.bodegaaurrera.com.mx/search?q={q}",
    "Chedraui": "https://www.chedraui.com.mx/search?q={q}",
    "Mercado Libre MX": "https://listado.mercadolibre.com.mx/{q}",
    "Soriana": "https://www.soriana.com/buscar?q={q}",
    "Liverpool": "https://www.liverpool.com.mx/tienda?s={q}",
    "Amazon MX": "https://www.amazon.com.mx/s?k={q}",
}

KEYWORDS_LIQUIDACION = ("liquidacion","liquidación","remate","outlet","ultima pieza","última pieza","ultimas piezas","últimas piezas","saldo","saldos","caja abierta","open box","precio especial")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Cache-Control": "no-cache",
}

def normalizar_texto(texto):
    return re.sub(r"\s+", " ", texto or "").strip()

def extraer_precios(texto):
    encontrados = re.findall(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)", texto or "")
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
    return hashlib.sha256(f"{tienda}|{url or titulo}".encode("utf-8")).hexdigest()

def es_url_producto(url, base):
    if not url:
        return False
    parsed = urlparse(url)
    base_host = urlparse(base).netloc
    if not parsed.netloc or parsed.netloc != base_host:
        return False
    u = url.lower()
    if any(x in u for x in ("/search?", "/buscar?", "/tienda?s=", "/listado/")):
        return False
    if u.endswith("/ofertas") or "/ofertas?" in u:
        return False
    return True

def recorrer_json(obj):
    if isinstance(obj, dict):
        yield obj
        for valor in obj.values():
            yield from recorrer_json(valor)
    elif isinstance(obj, list):
        for valor in obj:
            yield from recorrer_json(valor)

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
            offers = obj.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                offers = {}
            actual = offers.get("price") or offers.get("lowPrice")
            anterior = None
            try:
                actual = float(str(actual).replace(",", "")) if actual else None
                anterior = float(str(anterior).replace(",", "")) if anterior else None
            except ValueError:
                actual = anterior = None
            if not actual:
                continue
            if not es_url_producto(url, base_url):
                continue
            if anterior and anterior <= actual:
                anterior = None
            resultados.append({"tienda": tienda,"titulo": titulo[:180],"precio_actual": actual,"precio_anterior": anterior,"descuento": calcular_descuento(anterior, actual) if anterior else 0,"url": url,"liquidacion": any(k in titulo.lower() for k in KEYWORDS_LIQUIDACION)})
    return resultados

def extraer_tarjetas(soup, tienda, base_url, liquidacion_contexto=False):
    resultados = []
    selectores = ["article","[data-testid*='product']","[data-testid*='Product']","[class*='product-card']","[class*='ProductCard']","[class*='product-tile']","[class*='ProductTile']","li[class*='product']"]
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
            actual = precios[0]
            anterior = next((p for p in precios[1:] if p > actual), None)
            dcto = calcular_descuento(anterior, actual) if anterior else 0
            es_liq = liquidacion_contexto or any(k in texto.lower() for k in KEYWORDS_LIQUIDACION)
            if not anterior and not es_liq:
                continue
            if not es_url_producto(url, base_url):
                continue
            resultados.append({"tienda": tienda,"titulo": titulo[:180],"precio_actual": actual,"precio_anterior": anterior,"descuento": dcto,"url": url,"liquidacion": es_liq})
    return resultados

def buscar_tienda(nombre, plantilla, session):
    resultados = []
    base_url = plantilla.split("{q}", 1)[0]
    for q in BUSQUEDAS:
        url = plantilla.format(q=quote_plus(q))
        try:
            response = None
            for intento in range(3):
                response = session.get(url, timeout=20)
                if response.status_code == 404:
                    print(f"{nombre}: HTTP 404 para {q}; se omite esa búsqueda.")
                    break
                if response.status_code == 429 or response.status_code >= 500:
                    espera = 2 ** intento
                    print(f"{nombre}: HTTP {response.status_code} para {q}; reintento en {espera}s.")
                    if intento < 2:
                        time.sleep(espera)
                        continue
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
    return resultados

def buscar_todas():
    session = requests.Session()
    session.headers.update(HEADERS)
    salida = []
    for nombre, plantilla in TIENDAS.items():
        salida.extend(buscar_tienda(nombre, plantilla, session))
    return salida
