# scrapers/api_stores.py
import random
import logging
from curl_cffi import requests as curl_requests
from config.endpoints import ENDPOINTS_REALES, STORES_FALLBACK

logger = logging.getLogger("DexterH4ck.ApiStores")

class ApiStoresScraper:
    def __init__(self, context=None):
        self.context = context
        # Autenticación TLS/JA3 automática para Bodega Aurrera, Walmart y Mercado Libre
        self.session = curl_requests.Session(impersonate="chrome")
        
        self.walmart_api = getattr(self.context, "WALMART_GRAPHQL_URL", None) or ENDPOINTS_REALES["WALMART_GRAPHQL"]
        self.chedraui_api = getattr(self.context, "CHEDRAUI_VTEX", None) or ENDPOINTS_REALES["CHEDRAUI_VTEX"]
        self.mercadolibre_api = getattr(self.context, "MERCADOLIBRE_API", None) or ENDPOINTS_REALES["MERCADOLIBRE_API"]

        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        })

    def fetch_walmart_bodega_graphql(self, tienda="walmart", search_query="liquidacion", store_id=None):
        url = self.walmart_api
        default_store = STORES_FALLBACK["WALMART_STORE_ID"] if tienda == "walmart" else STORES_FALLBACK["BODEGA_STORE_ID"]
        final_store_id = store_id or getattr(self.context, f"{tienda.upper()}_STORE_ID", default_store)

        payload = {
            "query": "query SearchAndFilter($searchQuery: String!, $facetFilters: String, $page: Int, $size: Int, $storeId: String!) { search(query: $searchQuery, facetFilters: $facetFilters, page: $page, size: $size, storeId: $storeId) { products { id name brand canonicalUrl priceInfo { currentPrice { price } wasPrice { price } } } } }",
            "variables": {"searchQuery": search_query, "facetFilters": "[]", "page": 1, "size": 50, "storeId": str(final_store_id)}
        }
        
        headers = {
            "Content-Type": "application/json",
            "Origin": f"https://www.{tienda}.com.mx",
            "Referer": f"https://www.{tienda}.com.mx/search?q={search_query}"
        }
        
        try:
            # Se fuerza un timeout estricto de 20 segundos para evitar que el runner se quede colgado eternamente
            response = self.session.post(url, json=payload, headers=headers, timeout=20.0)
            if response.status_code == 200:
                return response.json()
            # CORREGIDO: Se definen explícitamente los códigos de restricción para validar la sintaxis
            elif response.status_code in (401, 403, 412, 429):
                logger.warning(f"[{tienda.upper()}] Acceso denegado o limitado temporalmente por el servidor ({response.status_code}).")
        except Exception as e:
            logger.error(f"Error en transporte GraphQL de {tienda}: {str(e)}")
        return {}

    def fetch_chedraui_vtex(self):
        params = {"_from": "0", "_to": "49", "O": "OrderByBestDiscountDESC"}
        try:
            response = self.session.get(self.chedraui_api, params=params, timeout=20.0)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Error en catálogo VTEX Chedraui: {str(e)}")
        return []

    def fetch_mercado_libre_api(self, query="liquidacion"):
        params = {"q": query, "limit": "50", "sort": "discount_desc"}
        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"}
        try:
            response = self.session.get(self.mercadolibre_api, params=params, headers=headers, timeout=15.0)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Error en API de Mercado Libre: {str(e)}")
        return {}
