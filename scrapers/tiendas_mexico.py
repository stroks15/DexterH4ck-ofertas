import requests
from bs4 import BeautifulSoup
import re

BUSQUEDAS = ["iphone", "laptop", "smart tv", "playstation", "xbox", "tenis", "refrigerador"]

TIENDAS = {
    "Liverpool": "https://www.liverpool.com.mx/tienda?s={q}",
    "Walmart": "https://www.walmart.com.mx/search?q={q}",
    "Bodega Aurrera": "https://www.bodegaaurrera.com.mx/search?q={q}",
    "Chedraui": "https://www.chedraui.com.mx/search?q={q}",
}


def limpiar_precio(valor):
    if not valor:
        return None
    numeros = re.sub(r"[^0-9]", "", valor)
    return int(numeros) if numeros else None


def buscar_tienda(nombre, url):
    ofertas = []
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        html = requests.get(url, headers=headers, timeout=20).text
        soup = BeautifulSoup(html, "html.parser")

        for item in soup.find_all(["article", "div"], limit=80):
            texto = item.get_text(" ", strip=True)
            precios = re.findall(r"\$[0-9,]+", texto)

            if len(precios) >= 2:
                actual = limpiar_precio(precios[0])
                anterior = limpiar_precio(precios[1])

                if actual and anterior and anterior > actual:
                    descuento = round((1-(actual/anterior))*100)
                    if descuento >= 60:
                        ofertas.append({
                            "tienda": nombre,
                            "titulo": texto[:120],
                            "precio_actual": actual,
                            "precio_anterior": anterior,
                            "descuento": descuento,
                            "url": url
                        })
    except Exception as e:
        print(nombre, e)

    return ofertas


def buscar_todas():
    resultados = []
    for nombre, plantilla in TIENDAS.items():
        for q in BUSQUEDAS:
            resultados.extend(buscar_tienda(nombre, plantilla.format(q=q)))
    return resultados
