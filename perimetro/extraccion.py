# perimetro/extraccion.py
# Extractores por estrategia. Estructurado primero, selectores al final.
from __future__ import annotations

import html as _html
import json
import re
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

try:
    from core.price_sanitizer import parse_price, sanear_par_precios  # type: ignore
except ImportError:  # pragma: no cover
    def parse_price(valor: Any) -> float | None:  # type: ignore
        if valor is None or isinstance(valor, bool):
            return None
        if isinstance(valor, (int, float)):
            return float(valor) if 0 < float(valor) < 2_000_000 else None
        limpio = re.sub(r"[^\d.,]", "", str(valor))
        if not limpio:
            return None
        if "," in limpio and "." in limpio:
            limpio = limpio.replace(",", "") if limpio.rfind(".") > limpio.rfind(",") else limpio.replace(".", "").replace(",", ".")
        elif "," in limpio:
            limpio = limpio.replace(",", ".") if len(limpio.split(",")[-1]) == 2 else limpio.replace(",", "")
        try:
            numero = float(limpio)
        except ValueError:
            return None
        return numero if 0 < numero < 2_000_000 else None

    def sanear_par_precios(actual, anterior, _texto=""):  # type: ignore
        return actual, anterior, ""

PRECIO = re.compile(r"\$\s*([\d][\d.,]{1,14})")
DESCUENTO = re.compile(r"(\d{1,2})\s*%\s*(?:de\s*)?(?:descuento|off|menos)", re.I)
TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>", re.S)
ATRIBUTO = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")


def _attrs(bruto: str) -> dict[str, str]:
    salida = {}
    for encontrado in ATRIBUTO.finditer(bruto or ""):
        salida[encontrado.group(1).lower()] = _html.unescape(encontrado.group(2).strip("\"'"))
    return salida


def _texto_html(trozo: str) -> str:
    limpio = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", trozo, flags=re.S | re.I)
    limpio = re.sub(r"<[^>]+>", " ", limpio)
    return re.sub(r"\s+", " ", _html.unescape(limpio)).strip()


def _absoluta(base: str, enlace: str) -> str:
    if not enlace:
        return ""
    if enlace.startswith("//"):
        return "https:" + enlace
    if enlace.startswith("http"):
        return enlace
    return urljoin(base, enlace)


def _precios(texto: str) -> list[float]:
    salida = []
    for encontrado in PRECIO.finditer(texto or ""):
        valor = parse_price(encontrado.group(1))
        if valor:
            salida.append(valor)
    return salida


def _par(actual: Any, anterior: Any, texto: str = "") -> tuple[float | None, float | None, int]:
    precio, referencia, _ = sanear_par_precios(parse_price(actual), parse_price(anterior), texto)
    descuento = 0
    if precio and referencia and referencia > precio:
        descuento = round((1 - precio / referencia) * 100)
    else:
        hallado = DESCUENTO.search(texto or "")
        if hallado:
            descuento = int(hallado.group(1))
    return precio, referencia, descuento


def _fila(tienda: str, titulo: str, url: str, precio: float | None, referencia: float | None, descuento: int, origen: str, ident: str = "", imagen: str = "") -> dict | None:
    if not titulo or not url or not precio:
        return None
    return {"tienda": tienda, "titulo": re.sub(r"\s+", " ", titulo).strip()[:300], "url": url, "precio_actual": precio, "precio_anterior": referencia if referencia and referencia > precio else None, "descuento": max(0, min(99, descuento)), "origen_link": origen, "tipo_fuente": "ONLINE", "id": ident or None, "imagen": imagen or None}


def _tokenizar(regla: str) -> tuple[str, list[str], str, dict[str, str]]:
    tag = ""
    clases: list[str] = []
    ident = ""
    attrs: dict[str, str] = {}
    resto = regla.strip()
    encontrado = re.match(r"^([a-zA-Z][a-zA-Z0-9-]*)", resto)
    if encontrado:
        tag = encontrado.group(1).lower()
        resto = resto[encontrado.end():]
    for clase in re.findall(r"\.([A-Za-z0-9_-]+)", resto):
        clases.append(clase)
    ident_encontrado = re.search(r"#([A-Za-z0-9_-]+)", resto)
    if ident_encontrado:
        ident = ident_encontrado.group(1)
    for attr in re.finditer(r"\[\s*([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*(?:=\s*(\"[^\"]*\"|'[^']*'|[^\]]*))?\]", resto):
        attrs[attr.group(1).lower()] = (attr.group(2) or "").strip("\"'")
    return tag, clases, ident, attrs


def _coincide(attrs: dict[str, str], tag: str, clases: list[str], ident: str, filtros: dict[str, str]) -> bool:
    if tag and tag != "*" and tag != attrs.get("__tag"):
        return False
    if ident and attrs.get("id") != ident:
        return False
    if clases and not set(clases).issubset(set((attrs.get("class") or "").split())):
        return False
    for clave, valor in filtros.items():
        if clave not in attrs or (valor and valor != attrs.get(clave)):
            return False
    return True


def nodos(cuerpo: str, regla: str) -> list[dict[str, Any]]:
    if not cuerpo or not regla:
        return []
    partes = [p for p in regla.split() if p]
    tag, clases, ident, filtros = _tokenizar(partes[-1])
    salida: list[dict[str, Any]] = []
    for encontrado in TAG.finditer(cuerpo):
        if encontrado.group(1) == "/":
            continue
        attrs = _attrs(encontrado.group(3))
        attrs["__tag"] = encontrado.group(2).lower()
        if not _coincide(attrs, tag, clases, ident, filtros):
            continue
        inicio = encontrado.end()
        if partes[:-1] and not _tiene_ancestro(cuerpo, encontrado.start(), partes[:-1]):
            continue
        salida.append({"attrs": attrs, "html": cuerpo[inicio:_cierre(cuerpo, inicio, encontrado.group(2))]})
    return salida


def _cierre(cuerpo: str, inicio: int, etiqueta: str) -> int:
    patron = re.compile(rf"</?{re.escape(etiqueta)}\b", re.I)
    nivel = 1
    for encontrado in patron.finditer(cuerpo, inicio):
        nivel += -1 if cuerpo[encontrado.start() + 1] == "/" else 1
        if nivel == 0:
            return encontrado.start()
    return min(len(cuerpo), inicio + 4000)


def _tiene_ancestro(cuerpo: str, posicion: int, partes: list[str]) -> bool:
    previo = cuerpo[max(0, posicion - 4000):posicion]
    for parte in partes:
        tag, clases, ident, filtros = _tokenizar(parte)
        hallado = False
        for encontrado in TAG.finditer(previo):
            if encontrado.group(1) == "/":
                continue
            attrs = _attrs(encontrado.group(3))
            attrs["__tag"] = encontrado.group(2).lower()
            if _coincide(attrs, tag, clases, ident, filtros):
                hallado = True
                break
        if not hallado:
            return False
    return True


def vtex(payload: Any, tienda: str, base: str) -> list[dict]:
    filas = []
    for producto in payload if isinstance(payload, list) else []:
        if not isinstance(producto, dict):
            continue
        mejor = None
        for sku in producto.get("items") or []:
            for vendedor in (sku or {}).get("sellers") or []:
                oferta = (vendedor or {}).get("commertialOffer") or {}
                if (parse_price(oferta.get("AvailableQuantity")) or 0) <= 0:
                    continue
                precio = parse_price(oferta.get("Price"))
                if not precio:
                    continue
                referencia = None
                for campo in ("ListPrice", "PriceWithoutDiscount"):
                    candidato = parse_price(oferta.get(campo))
                    if candidato and candidato > precio:
                        referencia = candidato
                        break
                ranking = ((1 - precio / referencia) if referencia else 0, -precio)
                if mejor is None or ranking > mejor[0]:
                    mejor = (ranking, precio, referencia)
        if not mejor:
            continue
        precio, referencia, descuento = _par(mejor[1], mejor[2])
        fila = _fila(tienda, str(producto.get("productName") or ""), _absoluta(base, str(producto.get("link") or "")), precio, referencia, descuento, "vtex_catalogo_publico", str(producto.get("productId") or ""))
        if fila:
            filas.append(fila)
    return filas


def sfcc(payload: Any, tienda: str, base: str) -> list[dict]:
    filas = []
    grupos: list[Any] = []
    if isinstance(payload, dict):
        for clave in ("suggestions", "products", "productSuggestions"):
            valor = payload.get(clave)
            if isinstance(valor, list):
                grupos.extend(valor)
            elif isinstance(valor, dict):
                for interno in valor.values():
                    if isinstance(interno, list):
                        grupos.extend(interno)
    for item in grupos:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("name") or item.get("productName") or item.get("title") or "")
        enlace = str(item.get("url") or item.get("link") or item.get("productUrl") or "")
        precio, referencia, descuento = _par(item.get("price") or item.get("salePrice"), item.get("listPrice") or item.get("standardPrice"), nombre)
        fila = _fila(tienda, nombre, _absoluta(base, enlace), precio, referencia, descuento, "sfcc_sugerencias", str(item.get("id") or ""))
        if fila:
            filas.append(fila)
    return filas


def _recorrer(nodo: Any, salida: list[dict]) -> None:
    if isinstance(nodo, dict):
        precio = None
        for clave in ("currentPrice", "salePrice", "price", "finalPrice", "sellingPrice"):
            if clave in nodo:
                precio = nodo.get(clave)
                break
        nombre = nodo.get("name") or nodo.get("title") or nodo.get("productName") or nodo.get("displayName")
        enlace = nodo.get("url") or nodo.get("link") or nodo.get("productUrl") or nodo.get("canonicalUrl")
        if isinstance(precio, dict):
            precio = precio.get("value") or precio.get("amount") or precio.get("current")
        if precio is not None and nombre and enlace and isinstance(precio, (int, float, str)):
            salida.append({"nombre": str(nombre), "url": str(enlace), "precio": precio, "antes": nodo.get("listPrice") or nodo.get("strikethroughPrice") or nodo.get("wasPrice"), "id": nodo.get("id") or nodo.get("sku") or nodo.get("usItemId")})
        for valor in nodo.values():
            _recorrer(valor, salida)
    elif isinstance(nodo, list):
        for valor in nodo:
            _recorrer(valor, salida)


def nextdata(cuerpo: str, tienda: str, base: str) -> list[dict]:
    encontrados = []
    for patron in (r"<script[^>]+id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", r"window\.__NUXT__\s*=\s*(\{.*?\});", r"__APOLLO_STATE__\s*=\s*(\{.*?\});"):
        for hallado in re.finditer(patron, cuerpo or "", re.S):
            try:
                data = json.loads(hallado.group(1))
            except ValueError:
                continue
            crudos: list[dict] = []
            _recorrer(data, crudos)
            for crudo in crudos:
                precio, referencia, descuento = _par(crudo["precio"], crudo.get("antes"))
                fila = _fila(tienda, crudo["nombre"], _absoluta(base, crudo["url"]), precio, referencia, descuento, "next_data", str(crudo.get("id") or ""))
                if fila:
                    encontrados.append(fila)
    return encontrados


def jsonld(cuerpo: str, tienda: str, base: str) -> list[dict]:
    filas = []
    for hallado in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", cuerpo or "", re.S):
        try:
            data = json.loads(_html.unescape(hallado.group(1)).strip())
        except ValueError:
            continue
        for bloque in data if isinstance(data, list) else [data]:
            if not isinstance(bloque, dict):
                continue
            tipo = str(bloque.get("@type") or "")
            if "ItemList" in tipo:
                for elemento in bloque.get("itemListElement") or []:
                    if isinstance(elemento, dict):
                        item = elemento.get("item") or elemento
                        if isinstance(item, dict):
                            filas.extend(_de_producto(item, tienda, base))
            elif "Product" in tipo:
                filas.extend(_de_producto(bloque, tienda, base))
    return filas


def _de_producto(producto: dict, tienda: str, base: str) -> list[dict]:
    ofertas = producto.get("offers") or {}
    if isinstance(ofertas, list):
        ofertas = ofertas[0] if ofertas else {}
    if not isinstance(ofertas, dict):
        ofertas = {}
    precio, referencia, descuento = _par(ofertas.get("price") or ofertas.get("lowPrice"), producto.get("highPrice"))
    fila = _fila(tienda, str(producto.get("name") or ""), _absoluta(base, str(producto.get("url") or ofertas.get("url") or "")), precio, referencia, descuento, "jsonld", str(producto.get("sku") or producto.get("productID") or ""), str(producto.get("image") or ""))
    return [fila] if fila else []


def amazon(cuerpo: str, tienda: str, base: str, selectores: dict[str, str]) -> list[dict]:
    filas = []
    for tarjeta in nodos(cuerpo, selectores.get("card", "div[data-component-type=\"s-search-result\"]")):
        attrs = tarjeta["attrs"]
        enlace = ""
        for ancla in nodos(tarjeta["html"], selectores.get("link", "h2 a")):
            enlace = ancla["attrs"].get("href", "")
            if enlace:
                break
        titulo = ""
        for nodo_titulo in nodos(tarjeta["html"], selectores.get("title", "h2 span")):
            titulo = _texto_html(nodo_titulo["html"])
            if titulo:
                break
        texto = _texto_html(tarjeta["html"])
        precios = _precios(texto)
        antes = None
        for nodo_antes in nodos(tarjeta["html"], selectores.get("antes", "span.a-text-price span.a-offscreen")):
            valor = parse_price(_texto_html(nodo_antes["html"]))
            if valor:
                antes = valor
                break
        precio, referencia, descuento = _par(min(precios) if precios else None, antes, texto)
        fila = _fila(tienda, titulo, _absoluta(base, enlace), precio, referencia, descuento, "html_selectores", str(attrs.get("data-asin") or ""))
        if fila:
            filas.append(fila)
    return filas


def selectores(cuerpo: str, tienda: str, base: str, sel: dict[str, str]) -> list[dict]:
    filas = []
    if not sel.get("card"):
        return filas
    for tarjeta in nodos(cuerpo, sel["card"]):
        enlace = ""
        for ancla in nodos(tarjeta["html"], sel.get("link", "a[href]")):
            enlace = ancla["attrs"].get("href", "")
            if enlace:
                break
        if not enlace:
            enlace = tarjeta["attrs"].get("href", "")
        titulo = ""
        for nodo_titulo in nodos(tarjeta["html"], sel.get("title", "a")):
            titulo = _texto_html(nodo_titulo["html"]) or nodo_titulo["attrs"].get("title", "")
            if titulo:
                break
        if not titulo:
            titulo = tarjeta["attrs"].get("title", "")
        texto = _texto_html(tarjeta["html"])
        antes = None
        if sel.get("antes"):
            for nodo_antes in nodos(tarjeta["html"], sel["antes"]):
                valor = parse_price(_texto_html(nodo_antes["html"]))
                if valor:
                    antes = valor
                    break
        precio = None
        if sel.get("price"):
            for nodo_precio in nodos(tarjeta["html"], sel["price"]):
                valor = parse_price(_texto_html(nodo_precio["html"]))
                if valor:
                    precio = valor
                    break
        if precio is None:
            precios = _precios(texto)
            precio = min(precios) if precios else None
        precio, referencia, descuento = _par(precio, antes, texto)
        fila = _fila(tienda, titulo, _absoluta(base, enlace), precio, referencia, descuento, "html_selectores")
        if fila:
            filas.append(fila)
    return filas


def deduplicar(filas: Iterable[dict]) -> list[dict]:
    vistos = set()
    salida = []
    for fila in filas:
        clave = (fila.get("id") or "", urlparse(fila.get("url") or "").path)
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(fila)
    return salida
