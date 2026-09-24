import requests
from bs4 import BeautifulSoup
import re


KEYWORDS_LIVERPOOL = [
    "iphone",
    "laptop",
    "smart tv",
    "playstation",
    "xbox",
    "nintendo switch",
    "audifonos",
    "smartwatch",
    "tenis",
    "refrigerador",
    "lavadora",
]


def limpiar_precio(valor):
    if not valor:
        return None
    numeros = re.sub(r"[^0-9]", "", valor)
    return int(numeros) if numeros else None


def calcular_descuento(precio_anterior, precio_actual):
    if not precio_anterior or not precio_actual or precio_anterior <= precio_actual:
        return 0
    return round((1 - precio_actual / precio_anterior) * 100)


def buscar_liverpool():
    """Busca productos visibles de Liverpool.
    Liverpool puede cambiar su estructura HTML, por lo que este módulo
    está separado para poder ajustarlo sin tocar Telegram ni Mercado Libre.
    """
    ofertas = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for keyword in KEYWORDS_LIVERPOOL:
        url = f"https://www.liverpool.com.mx/tienda?s={keyword.replace(' ', '+')}"

        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
        except Exception as error:
            print(f"Error Liverpool {keyword}: {error}")
            continue

        soup = BeautifulSoup(response.text, "html.parser")

        for item in soup.find_all("article"):
            texto = item.get_text(" ", strip=True)
            precios = re.findall(r"\$[0-9,]+", texto)

            if len(precios) >= 2:
                actual = limpiar_precio(precios[0])
                anterior = limpiar_precio(precios[1])
                descuento = calcular_descuento(anterior, actual)

                if descuento >= 60:
                    ofertas.append({
                        "tienda": "Liverpool",
                        "titulo": texto[:120],
                        "precio_actual": actual,
                        "precio_anterior": anterior,
                        "descuento": descuento,
                        "url": url,
                    })

    return ofertas
