"""Fuente comunitaria LiquidaZona.

Replica el descubrimiento público de publicaciones de una tienda sin copiar
texto editorial completo. Extrae precio, referencia, UPC/SKU, título, imagen y
enlace de evidencia. Si la publicación contiene un enlace directo a la tienda,
lo conserva como url_producto.
"""
from __future__ import annotations
import os, re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE="https://liquidazona.com/tiendas/walmart/"
HEADERS={"User-Agent":"DexterH4ck-ofertas/2.0 (+deal-monitor; es-MX)","Accept-Language":"es-MX,es;q=0.9"}

PRICE_RE=re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)")
UPC_RE=re.compile(r"(?:UPC\s*/\s*SKU|UPC|SKU)\s*:\s*([0-9A-Za-z-]{6,})",re.I)

def _price(text):
    m=PRICE_RE.search(text or "")
    return float(m.group(1).replace(",","")) if m else None

def _article(response, session):
    soup=BeautifulSoup(response.text,"html.parser")
    h1=soup.find("h1")
    title=(h1.get_text(" ",strip=True) if h1 else "").strip()
    body=soup.get_text("\n",strip=True)
    def labelled(patterns):
        for pattern in patterns:
            m=re.search(pattern,body,re.I)
            if m:
                try: return float(m.group(1).replace(",",""))
                except ValueError: pass
        return None
    actual=labelled([r"Precio de liquidación:\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",r"Precio de oferta:\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"])
    original=labelled([r"Precio original:\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"])
    if not actual: actual=_price(title)
    if not actual: return None
    if original is not None and original<=actual: original=None
    upc=""
    m=UPC_RE.search(body)
    if m: upc=m.group(1)
    image=""
    og=soup.select_one("meta[property='og:image']")
    if og: image=urljoin(response.url,og.get("content") or "")
    product_url=""
    for a in soup.select("a[href]"):
        href=urljoin(response.url,a.get("href",""))
        host=urlparse(href).netloc.lower()
        if host.endswith(("walmart.com.mx","bodegaaurrera.com.mx")) and "/ip/" in urlparse(href).path.lower():
            product_url=href
            break
    discount=round((1-actual/original)*100) if original else 0
    store="Walmart MX"
    if "bodega" in title.lower() or "bodegaaurrera" in body.lower(): store="Bodega Aurrera"
    return {
        "tienda":store,
        "titulo":title[:180],
        "precio_actual":actual,
        "precio_anterior":original,
        "descuento":discount,
        "url":product_url or response.url,
        "url_producto":product_url,
        "url_evidencia":response.url,
        "upc":upc,
        "imagen":image,
        "liquidacion":True,
        "tipo_fuente":"COMUNIDAD",
        "origen":"LiquidaZona",
        "origen_link":"LiquidaZona",
        "verificado_en_tienda":False,
    }

def buscar_liquidazona_walmart(session=None):
    session=session or requests.Session()
    session.headers.update(HEADERS)
    paginas=max(1,int(os.getenv("LIQUIDAZONA_PAGES","2")))
    resultados=[]
    vistos=set()
    for page_no in range(1,paginas+1):
        url=BASE if page_no==1 else BASE.rstrip("/") + f"/page/{page_no}/"
        try:
            response=session.get(url,timeout=25)
            if response.status_code>=400:
                print(f"LiquidaZona/Walmart: HTTP {response.status_code} en {url}")
                continue
            soup=BeautifulSoup(response.text,"html.parser")
            links=[]
            for a in soup.select("h2 a[href], h3 a[href], article a[href], a[href]"):
                href=urljoin(response.url,a.get("href",""))
                if "/ofertas/" not in href: continue
                if href not in links: links.append(href)
            for href in links[:30]:
                if href in vistos: continue
                vistos.add(href)
                try:
                    detail=session.get(href,timeout=25)
                    if detail.status_code>=400: continue
                    item=_article(detail,session)
                    if item:
                        resultados.append(item)
                except requests.RequestException:
                    continue
        except requests.RequestException as exc:
            print(f"LiquidaZona/Walmart: {type(exc).__name__}: {exc}")
    print(f"LiquidaZona/Walmart: {len(resultados)} publicaciones procesadas")
    return resultados
