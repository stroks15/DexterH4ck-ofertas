# scrapers/tiendas_mexico.py
import html
import re
import logging
import random
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from core.product_identifiers import canonical_product_identifier

logger = logging.getLogger("DexterH4ck.TiendasMexico")

# Listado dinámico de User-Agents idénticos al pool del orquestador principal
USER_AGENTS_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def buscar_todas(filtro_tienda=None) -> list:
    """
    Motor Legacy robusto con soporte nativo de curl_cffi.
    Rastrea las búsquedas públicas cuando las APIs dedicadas están limitadas.
    """
    productos_encontrados = []
    
    # Inicialización del cliente emulando el stack criptográfico completo de Chrome
    session = curl_requests.Session(impersonate="chrome")
    
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive"
    })

    # Mapeo de términos de búsqueda genéricos de alta demanda en México
    terminos = ["liquidacion", "ofertas", "remate", "outlet"]
    
    # Diccionario de URLs públicas base mapeadas por tu bot
    urls_mapeadas = {
        "amazon": "https://amazon.com.mx",
        "soriana": "https://soriana.com",
        "liverpool": "https://liverpool.com.mx"
    }

    # Si se especifica un filtro (ej. desde el orquestador paralelo), acorta el bucle
    tiendas_a_procesar = [filtro_tienda.lower()] if filtro_tienda else urls_mapeadas.keys()

    for tienda in tiendas_a_procesar:
        if tienda not in urls_mapeadas:
            continue
            
        base_url = urls_mapeadas[tienda]
        for termino in terminos:
            target_url = f"{base_url}{termino}"
            try:
                logger.info(f"[{tienda.upper()}] Extrayendo listado público vía curl_cffi: {target_url}")
                
                # Timeout robusto de 25 segundos para evitar hilos colgados en GitHub Actions
                response = session.get(target_url, timeout=25.0)
                
                if response.status_code == 200:
                    html_content = response.text
                    items_procesados = _parsear_html_por_tienda(html_content, tienda, target_url)
                    productos_encontrados.extend(items_procesados)
                elif response.status_code == 503 and tienda == "amazon":
                    logger.warning("[AMAZON] El servidor devolvió 503 (Frecuencia alta). Aplicando enfriamiento...")
                else:
                    logger.error(f"[{tienda.upper()}] Respuesta inestable del servidor público: {response.status_code}")
                    
            except Exception as e:
                logger.error(f"[{tienda.upper()}] Excepción controlada de red en fallback público: {str(e)}")

    return productos_encontrados

def _parsear_html_por_tienda(html_source, tienda, origen_url) -> list:
    """Análisis sintáctico básico del HTML extraído por el cliente robusto."""
    soup = BeautifulSoup(html_source, "html.parser")
    resultados = []
    
    # --- Extractor Alternativo para Amazon MX ---
    if tienda == "amazon":
        for contenedor in soup.select("div[data-component-type='s-search-result']"):
            try:
                titulo_el = contenedor.select_one("h2 a span")
                link_el = contenedor.select_one("h2 a")
                precio_el = contenedor.select_one(".a-price-whole")
                
                if titulo_el and link_el and precio_el:
                    precio_actual = float(precio_el.text.replace(",", "").strip())
                    url_final = "https://amazon.com.mx" + link_el.get("href", "")
                    
                    resultados.append({
                        "id": contenedor.get("data-asin", str(random.randint(1000, 9999))),
                        "name": titulo_el.text.strip(),
                        "brand": "Amazon",
                        "url": url_final,
                        "precio_actual": precio_actual,
                        "precio_anterior": None,  # Se calcula automáticamente mediante tu historial JSON
                        "tienda": "Amazon MX",
                        "tipo_fuente": "WEB_FALLBACK"
                    })
            except Exception:
                continue

    # --- Extractor Alternativo para Soriana ---
    elif tienda == "soriana":
        for card in soup.select(".product-card"):
            try:
                link_el = card.select_one("a.product-title-link")
                precio_el = card.select_one(".value")
                
                if link_el and precio_el:
                    precio_actual = float(precio_el.text.replace("$", "").replace(",", "").strip())
                    resultados.append({
                        "id": card.get("data-pid", str(random.randint(1000, 9999))),
                        "name": link_el.text.strip(),
                        "brand": "Soriana",
                        "url": "https://soriana.com" + link_el.get("href", ""),
                        "precio_actual": precio_actual,
                        "precio_anterior": None,
                        "tienda": "Soriana",
                        "tipo_fuente": "WEB_FALLBACK"
                    })
            except Exception:
                continue

    return resultados


def es_url_producto(url, base_url=None):
    """Determina si una URL pertenece a una página de producto y no a un listado/búsqueda."""
    try:
        parsed = urlparse(url or "")
        host = parsed.netloc.lower().split(":", 1)[0]
        path = parsed.path.lower()
        if not host or not path or path == "/":
            return False
        if base_url:
            base_host = urlparse(base_url).netloc.lower().split(":", 1)[0]
            if base_host and not (host == base_host or host.endswith("." + base_host)):
                return False
        if any(x in path for x in ("/search", "/buscar", "/ofertas", "/oferta", "/catalogo", "/marcas", "/home", "/social/")):
            return False
        patrones = (
            "/ip/", "/dp/", "/gp/product/", "/mlm-", "/p/", "/pdp/",
            "/tienda/pdp/", "/producto/", "/product/", "/item/"
        )
        if any(p in path for p in patrones):
            return True
        # Amazon y Mercado Libre también pueden usar identificadores en la ruta.
        if re.search(r"/(?:dp|gp/product|itm|mlm)[-_]?[a-z0-9]+", path):
            return True
        return False
    except Exception:
        return False

from urllib.parse import urlparse
