import hashlib
import re
import requests
from bs4 import BeautifulSoup

BUSQUEDAS = [
    "iphone", "laptop", "smart tv", "playstation", "xbox",
    "nintendo switch", "audifonos", "smartwatch", "tenis",
    "refrigerador", "lavadora", "pantalla"
]

TIENDAS = {
    "Amazon MX": "https://www.amazon.com.mx/s?k={q}",
    "Mercado Libre MX": "https://listado.mercadolibre.com.mx/{q}",
    "Walmart MX": "https://www.walmart.com.mx/search?q={q}",
    "Chedraui": "https://www.chedraui.com.mx/search?q={q}",
}

KEYWORDS = [
    "remate", "liquidacion", "liquidación", "outlet",
    "caja abierta", "open box", "oferta", "descuento"
]


def extraer_precios(texto):
    valores = re.findall(r"\$\s?([0-9,]+(?:\.[0-9]{2})?)", texto or "")
    return [float(x.replace(',', '')) for x in valores]


def identificador(tienda, titulo):
    return hashlib.sha256(f"{tienda}-{titulo}".encode()).hexdigest()


def calcular_descuento(precios):
    if len(precios) < 2 or precios[1] <= 0:
        return 0, precios[0] if precios else 0, precios[0] if precios else 0
    anterior = max(precios)
    actual = min(precios)
    descuento = int(((anterior - actual) / anterior) * 100)
    return descuento, anterior, actual


def validar(tienda, titulo, precios):
    texto = titulo.lower()

    if tienda in ["Walmart MX", "Chedraui"]:
        if precios:
            return bool(re.search(r"\.(01|02|03)$", f"{precios[-1]:.2f}"))

    return any(k in texto for k in KEYWORDS) or calcular_descuento(precios)[0] >= 50


def buscar_tienda(nombre, url):
    resultados = []
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"},
            timeout=25
        )

        soup = BeautifulSoup(r.text, "html.parser")

        for bloque in soup.find_all(["article", "div"], limit=200):
            texto = bloque.get_text(" ", strip=True)
            precios = extraer_precios(texto)

            if not precios:
                continue

            descuento, anterior, actual = calcular_descuento(precios)

            if validar(nombre, texto, precios):
                resultados.append({
                    "id": identificador(nombre, texto[:150]),
                    "tienda": nombre,
                    "titulo": texto[:150],
                    "precio_anterior": anterior,
                    "precio_actual": actual,
                    "descuento": descuento,
                    "url": url
                })

    except Exception as e:
        print(f"{nombre}: {e}")

    return resultados


def buscar_todas():
    salida = []
    for tienda, url in TIENDAS.items():
        for q in BUSQUEDAS:
            salida.extend(buscar_tienda(tienda, url.format(q=q)))
    return salida
