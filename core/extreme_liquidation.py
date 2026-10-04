"""Detección conservadora de liquidaciones extremas y precios de $1 o similares.

Un precio extremo NO se considera automáticamente un error de precio ni una compra garantizada. El módulo separa señal, verificación y publicación.
"""

import re
from urllib.parse import urlparse
import requests

PRICE_RE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)")

OFFICIAL_HOSTS = {
    "walmart": ("walmart.com.mx","kiosco.www.walmart.com.mx"),
    "bodega aurrera": ("bodegaaurrera.com.mx",),
    "chedraui": ("chedraui.com.mx",),
    "coppel": ("coppel.com",),
    "liverpool": ("liverpool.com.mx",),
    "soriana": ("soriana.com",),
    "mercado libre": ("mercadolibre.com.mx","meli.la"),
    "amazon": ("amazon.com.mx","link.amazon","amzn.to"),
    "sanborns": ("sanborns.com.mx",)
}

def _host(url):
    try:
        return urlparse(str(url or "")).netloc.lower().split(":")[0]
    except Exception:
        return ""

def es_producto_oficial(url, tienda):
    host = _host(url)
    if not host:
        return False
    key = str(tienda or "").lower()
    for nombre, hosts in OFFICIAL_HOSTS.items():
        if nombre in key:
            return any(host == h or host.endswith("." + h) for h in hosts)
    return False

def es_precio_extremo(precio, referencia=None):
    try:
        precio = float(precio or 0)
        referencia = float(referencia or 0)
    except (TypeError, ValueError):
        return False
    if precio <= 0:
        return False
    if precio <= 10:
        return True
    if referencia > precio:
        return ((1 - precio / referencia) * 100) >= 95
    return False

def porcentaje_descuento(precio, referencia=None):
    try:
        precio = float(precio or 0)
        referencia = float(referencia or 0)
    except (TypeError, ValueError):
        return 0
    if precio <= 0 or referencia <= precio:
        return 0
    return round((1 - precio / referencia) * 100, 2)


def es_liquidacion_95_99(precio, referencia=None):
    descuento = porcentaje_descuento(precio, referencia)
    return 95 <= descuento <= 99.99


def nivel_extremo(precio, referencia=None):
    try:
        precio = float(precio or 0)
        referencia = float(referencia or 0)
    except (TypeError, ValueError):
        return "normal"
    if precio <= 1:
        return "ULTRA_EXTREMO"
    if precio <= 10:
        return "EXTREMO"
    if referencia > precio and ((1 - precio / referencia) * 100) >= 99:
        return "ULTRA_EXTREMO"
    if referencia > precio and ((1 - precio / referencia) * 100) >= 95:
        return "EXTREMO"
    return "normal"

def senal_terminacion_walmart(precio, tienda):
    if "walmart" not in str(tienda or "").lower():
        return False
    try:
        texto = f"{float(precio):.2f}"
    except (TypeError, ValueError):
        return False
    return texto.endswith((".01",".02",".03"))

def _precios_en_html(html):
    valores=[]
    for valor in PRICE_RE.findall(html or ""):
        try:
            numero=float(valor.replace(",",""))
            if 0 < numero <= 2_000_000:
                valores.append(numero)
        except ValueError:
            pass
    return valores

def verificar_precio_producto(url, precio_esperado, tienda, session=None):
    if not es_producto_oficial(url, tienda):
        return False, "host_no_oficial"
    try:
        session=session or requests.Session()
        response=session.get(url,headers={"User-Agent":"Mozilla/5.0 (compatible; DexterH4ck-ofertas/1.0)","Accept-Language":"es-MX,es;q=0.9,en;q=0.7"},timeout=15,allow_redirects=True)
        if response.status_code >= 400:
            return False, f"http_{response.status_code}"
        if not es_producto_oficial(response.url, tienda):
            return False, "redireccion_no_oficial"
        precios=_precios_en_html(response.text)
        esperado=float(precio_esperado)
        if any(abs(p-esperado) <= max(0.05,esperado*0.001) for p in precios):
            return True, "precio_en_pagina"
        return False, "precio_no_encontrado"
    except requests.RequestException as exc:
        return False, f"error_red:{type(exc).__name__}"

def analizar_precio_extremo(item, session=None):
    precio=item.get("precio_actual",item.get("price",0))
    referencia=item.get("precio_anterior",item.get("previous_price",0))
    tienda=item.get("tienda",item.get("store",""))
    url=item.get("url","")
    descuento=porcentaje_descuento(precio, referencia)
    nivel=nivel_extremo(precio,referencia)
    señales=[]
    if nivel != "normal":
        señales.append("precio_extremo")
    if senal_terminacion_walmart(precio,tienda):
        señales.append("walmart_terminacion_01_02_03")
    oficial=es_producto_oficial(url,tienda)
    if oficial:
        señales.append("url_tienda_oficial")
    verificado=False
    evidencia="no_verificado"
    if nivel != "normal" and oficial:
        verificado,evidencia=verificar_precio_producto(url,precio,tienda,session=session)
    return {
        "es_extremo": nivel != "normal",
        "nivel": nivel,
        "descuento_calculado": descuento,
        "es_95_99": es_liquidacion_95_99(precio, referencia),
        "senales": señales,
        "url_oficial": oficial,
        "precio_verificado": verificado,
        "evidencia_precio": evidencia,
        "publicable_como_extremo": bool(nivel != "normal" and oficial and verificado),
        "publicable_como_95_99": bool(es_liquidacion_95_99(precio, referencia) and oficial and verificado)
    }
