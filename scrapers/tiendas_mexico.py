import hashlib
import re
import requests
from bs4 import BeautifulSoup

BUSQUEDAS = ["iphone", "laptop", "smart tv", "playstation", "xbox", "tenis", "refrigerador"]

TIENDAS = {
    "Amazon MX": "https://www.amazon.com.mx/s?k={q}",
    "Mercado Libre MX": "https://listado.mercadolibre.com.mx/{q}",
    "Walmart MX": "https://www.walmart.com.mx/search?q={q}",
    "Chedraui": "https://www.chedraui.com.mx/search?q={q}",
}

KEYWORDS = ["remate", "liquidacion", "liquidación", "outlet", "caja abierta", "oferta"]


def precio(texto):
    m = re.search(r"\$\s?([0-9,]+(?:\.[0-9]{2})?)", texto or "")
    return float(m.group(1).replace(',', '')) if m else None


def identificador(tienda, titulo):
    return hashlib.sha256(f"{tienda}-{titulo}".encode()).hexdigest()


def validar(tienda, titulo, actual):
    texto = titulo.lower()
    if tienda in ["Walmart MX", "Chedraui"]:
        return bool(re.search(r"\.(01|02|03)$", str(actual)))
    return any(k in texto for k in KEYWORDS)


def buscar_tienda(nombre, url):
    resultados=[]
    try:
        r=requests.get(url,headers={"User-Agent":"Mozilla/5.0"},timeout=25)
        soup=BeautifulSoup(r.text,"html.parser")
        for bloque in soup.find_all(["article","div"],limit=120):
            texto=bloque.get_text(" ",strip=True)
            p=precio(texto)
            if p and validar(nombre,texto,p):
                resultados.append({
                    "id": identificador(nombre,texto[:150]),
                    "tienda": nombre,
                    "titulo": texto[:150],
                    "precio_actual": p,
                    "url": url
                })
    except Exception as e:
        print(nombre,e)
    return resultados


def buscar_todas():
    salida=[]
    for tienda,url in TIENDAS.items():
        for q in BUSQUEDAS:
            salida.extend(buscar_tienda(tienda,url.format(q=q)))
    return salida
