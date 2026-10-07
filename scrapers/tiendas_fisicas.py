import os
import re
import time
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.liquidation_engine import detect_priority_brand, infer_category

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9",
}

PRECIO_RE = re.compile(
    r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)"
)

# Son sucursales físicas/remates que el usuario pidió vigilar.
# El scraper solo publica una oportunidad cuando encuentra precio + evidencia
# pública en una página indexada; no inventa existencias en tienda.
TIENDAS_FISICAS = [
    {
        "nombre": "Coppel Remate Los Reyes",
        "direccion": "Carretera Federal México-Puebla Km 17.5, Los Reyes La Paz, Estado de México",
        "consultas": ["Coppel Remate Los Reyes liquidacion oferta", "Coppel Remate Los Reyes precio remate"],
    },
    {
        "nombre": "Bodega De Remates Los Pepos",
        "direccion": "Justo Sierra 43, Santa Cruz Meyehualco, Iztapalapa, CDMX",
        "consultas": ["Bodega de Remates Los Pepos liquidacion oferta", "Bodega de Remates Los Pepos precio"],
    },
    {
        "nombre": "Coppel Fernando Arruti",
        "direccion": "Calz. Ignacio Zaragoza 2514, Santa Martha Acatitla Norte, Iztapalapa, CDMX",
        "consultas": ["Coppel Fernando Arruti liquidacion oferta", "Coppel Fernando Arruti precio remate"],
    },
    {
        "nombre": "Liverpool Ciudad Jardín",
        "direccion": "Av. Bordo de Xochiaca 3, Plaza Ciudad Jardín, Nezahualcóyotl, Estado de México",
        "consultas": ["Liverpool Ciudad Jardin liquidacion oferta", "Liverpool Ciudad Jardin remate precio"],
    },
    {
        "nombre": "Liverpool Parque Tezontle",
        "direccion": "Av. Canal de Tezontle 851, Iztapalapa, CDMX",
        "consultas": ["Liverpool Parque Tezontle liquidacion oferta", "Liverpool Parque Tezontle remate precio"],
    },
    {
        "nombre": "Coppel Santa Martha",
        "direccion": "Av. Ermita Iztapalapa 4170, Iztapalapa, CDMX",
        "consultas": ["Coppel Santa Martha liquidacion oferta", "Coppel Santa Martha remate precio"],
    },
    {
        "nombre": "Walmart Plaza Oriente",
        "direccion": "Av. Canal de Tezontle 1520, Iztapalapa, CDMX",
        "consultas": ["Walmart Plaza Oriente liquidacion oferta", "Walmart Plaza Oriente precio oferta"],
    },
    {
        "nombre": "Bodega Aurrera El Salado",
        "direccion": "Zona El Salado, Iztapalapa, CDMX",
        "consultas": ["Bodega Aurrera El Salado liquidacion oferta", "Bodega Aurrera El Salado precio"],
    },
    {
        "nombre": "Soriana Híper Plaza Sendero Ixtapaluca",
        "direccion": "Plaza Sendero Ixtapaluca, Ixtapaluca, Estado de México",
        "consultas": ["Soriana Sendero Ixtapaluca liquidacion oferta", "Soriana Sendero Ixtapaluca precio remate"],
    },
]

PALABRAS = (
    "liquidacion", "liquidación", "remate", "oferta", "ofertas", "outlet",
    "saldo", "saldos", "ultima pieza", "última pieza", "precio especial",
    "rebaja", "descuento",
)


def precios(texto):
    encontrados = []
    for valor in PRECIO_RE.findall(texto or ""):
        try:
            numero = float(valor.replace(",", ""))
            if 1 <= numero <= 2_000_000:
                encontrados.append(numero)
        except ValueError:
            pass
    return encontrados


def extraer_destino(href):
    if not href:
        return ""
    href = unquote(href.strip())
    if href.startswith("/url?"):
        match = re.search(r"(?:\?|&)q=([^&]+)", href)
        if match:
            href = unquote(match.group(1))
    if href.startswith("/"):
        href = urljoin("https://www.google.com", href)
    parsed = urlparse(href)
    if parsed.scheme not in ("http", "https"):
        return ""
    if parsed.netloc.lower() in {"www.google.com", "google.com"}:
        return ""
    return href


def _titulo_resultado(node, fallback):
    h3 = node.find("h3")
    if h3:
        texto = h3.get_text(" ", strip=True)
        if texto:
            return texto
    return fallback[:180]


def buscar_tiendas_fisicas(session=None):
    if "Google" in {x.strip() for x in os.environ.get("PREFLIGHT_SKIP_SOURCES", "").split(",") if x.strip()}:
        print("Tiendas físicas: Google omitido por preflight; no se generan errores 429.")
        return []
    session = session or requests.Session()
    session.headers.update(HEADERS)
    resultados = []
    vistos = set()
    for tienda in TIENDAS_FISICAS:
        google_bloqueado = False
        for consulta in tienda["consultas"]:
            if google_bloqueado:
                break
            try:
                response = session.get(
                    "https://www.google.com/search",
                    params={"q": f'"{consulta}"', "hl": "es", "gl": "mx", "num": 8},
                    timeout=20,
                )
                if response.status_code == 429:
                    print(f"Fisicas/{tienda['nombre']}: Google HTTP 429; se detiene el resto de consultas de esta sucursal para no empeorar el bloqueo.")
                    google_bloqueado = True
                    continue
                if response.status_code >= 400:
                    print(f"Fisicas/{tienda['nombre']}: Google HTTP {response.status_code}")
                    continue

                soup = BeautifulSoup(response.text, "html.parser")
                for resultado in soup.select("div.MjjYud, div.g"):
                    enlace = resultado.find("a", href=True)
                    if not enlace:
                        continue
                    url = extraer_destino(enlace.get("href"))
                    if not url:
                        continue

                    contexto = " ".join(
                        x for x in [
                            resultado.get_text(" ", strip=True),
                            enlace.get_text(" ", strip=True),
                        ] if x
                    )
                    low = contexto.lower()
                    if not any(palabra in low for palabra in PALABRAS):
                        continue

                    valores = precios(contexto)
                    if not valores:
                        continue

                    actual = min(valores)
                    referencia = max((x for x in valores if x > actual), default=None)
                    titulo = _titulo_resultado(resultado, contexto)

                    # Evita resultados que solo corresponden al nombre de la sucursal
                    # sin un artículo/precio asociado.
                    if len(titulo) < 8 or actual <= 0:
                        continue

                    clave = f"{tienda['nombre']}|{titulo}|{actual}|{url}"
                    if clave in vistos:
                        continue
                    vistos.add(clave)

                    marca, _ = detect_priority_brand({"titulo": titulo})
                    categoria = infer_category({"titulo": titulo, "marca": marca})
                    descuento = round((1 - actual / referencia) * 100) if referencia and referencia > actual else 0

                    resultados.append({
                        "tienda": tienda["nombre"],
                        "sucursal": tienda["nombre"],
                        "direccion": tienda["direccion"],
                        "titulo": titulo[:180],
                        "marca": marca,
                        "categoria": categoria,
                        "precio_actual": actual,
                        "precio_anterior": referencia,
                        "descuento": descuento,
                        "url": url,
                        "liquidacion": True,
                        "outlet": any(x in low for x in ("outlet", "saldo", "remate")),
                        "tipo_fuente": "FISICA",
                        "origen": "Web pública indexada",
                        "origen_link": "Google",
                        "evidencia": contexto[:1200],
                    })

            except requests.RequestException as error:
                print(f"Fisicas/{tienda['nombre']}: {error}")
            except Exception as error:
                print(f"Fisicas/{tienda['nombre']}: error procesando resultados: {error}")

            time.sleep(0.35)

    print(f"Tiendas físicas: {len(resultados)} oportunidades públicas con precio")
    return resultados
