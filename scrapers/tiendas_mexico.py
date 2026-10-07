import re
import json
import logging
import random
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

logger = logging.getLogger("DexterH4ck.TiendasMexico")

class TiendasMexicoScraper:
    def __init__(self):
        # Todo el módulo unificado bajo curl_cffi con impersonate para protección total
        self.session = curl_requests.Session(impersonate="chrome")
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ]
        self.session.headers.update({
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        })

    def buscar_amazon(self) -> list:
        """
        Módulo Amazon MX: Cambia la búsqueda HTML ruidosa por el parseo de la API 
        interna de las páginas de 'Deals' de Amazon, capturando descuentos reales.
        """
        url = "https://amazon.com.mx"
        productos = []
        try:
            response = self.session.get(url, timeout=20.0)
            if response.status_code == 503:
                logger.warning("[AMAZON] Servidor saturado (503). Activando pausa táctica.")
                return []
                
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                # Buscamos el contenedor de datos ocultos nativo de Amazon (JSON integrado)
                vistas_datos = soup.find_all("div", {"data-viewport": True})
                
                for vista in vistas_datos:
                    try:
                        # Extraemos metadatos estructurados del producto sin tocar el HTML visual
                        info = json.loads(vista.get("data-viewport", "{}"))
                        payload = info.get("payload", {})
                        if not payload: continue
                        
                        titulo = payload.get("title", "")
                        asin = payload.get("asin", "")
                        
                        # Captura inteligente de datos numéricos de ofertas relámpago
                        precio_oferta = float(payload.get("currentPrice", 0))
                        precio_base = float(payload.get("wasPrice", 0)) or precio_oferta
                        
                        if asin and titulo and precio_oferta > 0:
                            productos.append({
                                "id": asin,
                                "name": titulo,
                                "brand": "Amazon Deal",
                                "canonical_url": f"https://amazon.com.mx{asin}",
                                "precio_actual": precio_oferta,
                                "precio_anterior": precio_base if precio_base > precio_oferta else None,
                                "tienda": "Amazon MX",
                                "image": payload.get("imageUrl", None)
                            })
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception as e:
            logger.error(f"Error crítico en módulo Amazon: {str(e)}")
        return productos

    def buscar_soriana(self) -> list:
        """
        Módulo Soriana: Intercepta de manera limpia el catálogo digital público de 
        ofertas usando parámetros limpios de ordenamiento.
        """
        url = "https://soriana.com"
        productos = []
        try:
            response = self.session.get(url, timeout=20.0)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                # Extraemos las tarjetas de productos renderizadas de manera segura
                grid_products = soup.find_all("div", class_="product-tile")
                
                for tile in grid_products:
                    try:
                        # Buscamos enlaces e identificadores únicos comerciales
                        link_tag = tile.find("a", class_="link")
                        if not link_tag: continue
                        
                        url_prod = "https://soriana.com" + link_tag.get("href", "")
                        pid = tile.get("data-pid", "")
                        titulo = tile.find("div", class_="pdp-link").get_text(strip=True) if tile.find("div", class_="pdp-link") else ""
                        
                        # Parseo de precios ignorando símbolos de moneda basura
                        price_sales = tile.find("span", class_="value")
                        precio_act = float(re.sub(r"[^\d.]", "", price_sales.get_text())) if price_sales else 0
                        
                        price_regular = tile.find("span", class_="strike-through")
                        precio_ant = float(re.sub(r"[^\d.]", "", price_regular.get_text())) if price_regular else None

                        if pid and precio_act > 0:
                            productos.append({
                                "id": pid,
                                "name": titulo,
                                "brand": "Soriana Comercial",
                                "canonical_url": url_prod,
                                "precio_actual": precio_act,
                                "precio_anterior": precio_ant if precio_ant and precio_ant > precio_act else None,
                                "tienda": "Soriana",
                                "image": tile.find("img", class_="tile-image").get("src", None) if tile.find("img", class_="tile-image") else None
                            })
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"Error crítico en módulo Soriana: {str(e)}")
        return productos

    def buscar_liverpool(self) -> list:
        """
        Módulo Liverpool: Extrae la metadata directa inyectada por el servidor dentro de las 
        etiquetas del catálogo de liquidaciones de prendas y tecnología.
        """
        url = "https://liverpool.com.mx"
        productos = []
        try:
            response = self.session.get(url, timeout=25.0)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                items = soup.find_all("li", class_="m-product__card")
                
                for item in items:
                    try:
                        link = item.find("a")
                        if not link: continue
                        url_prod = "https://liverpool.com.mx" + link.get("href", "")
                        
                        titulo = item.find("h5", class_="card-title").get_text(strip=True) if item.find("h5", class_="card-title") else ""
                        
                        # Liverpool maneja precios promocionales en etiquetas de color específico
                        p_promo = item.find("p", class_="a-card-discountPrice")
                        precio_act = float(re.sub(r"[^\d.]", "", p_promo.get_text())) if p_promo else 0
                        
                        p_original = item.find("p", class_="a-card-regularPrice")
                        precio_ant = float(re.sub(r"[^\d.]", "", p_original.get_text())) if p_original else None
                        
                        sku = url_prod.split("/")[-1]

                        if precio_act > 0 and titulo:
                            productos.append({
                                "id": sku,
                                "name": titulo,
                                "brand": "Liverpool Liquidación",
                                "canonical_url": url_prod,
                                "precio_actual": precio_act,
                                "precio_anterior": precio_ant if precio_ant and precio_ant > precio_act else None,
                                "tienda": "Liverpool"
                            })
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"Error crítico en módulo Liverpool: {str(e)}")
        return productos

# Adaptador de compatibilidad para el orquestador viejo y nuevo
def buscar_todas() -> list:
    scraper = TiendasMexicoScraper()
    resultados_totales = []
    
    # Ejecutamos las extracciones de manera limpia e independiente
    resultados_totales.extend(scraper.buscar_amazon())
    resultados_totales.extend(scraper.buscar_soriana())
    resultados_totales.extend(scraper.buscar_liverpool())
    
    return resultados_totales
