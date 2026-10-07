import random
import logging
from curl_cffi import requests as curl_requests
from config.endpoints import ENDPOINTS_REALES, STORES_FALLBACK

logger = logging.getLogger("DexterH4ck.ApiStores")

class ApiStoresScraper:
    def __init__(self, context):
        self.context = context
        # Activación manual de impersonate para emular el stack TLS/JA3 de Chrome
        self.session = curl_requests.Session(impersonate="chrome")
        
        # Resolución limpia de endpoints usando el fallback de producción
        self.walmart_api = getattr(self.context, "WALMART_GRAPHQL_URL", None) or ENDPOINTS_REALES["WALMART_GRAPHQL"]
        self.chedraui_api = getattr(self.context, "CHEDRAUI_VTEX", None) or ENDPOINTS_REALES["CHEDRAUI_VTEX"]
        self.mercadolibre_api = getattr(self.context, "MERCADOLIBRE_API", None) or ENDPOINTS_REALES["MERCADOLIBRE_API"]

        # Encabezados estándar de navegación orgánica para México
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        })

    def fetch_walmart_bodega_graphql(self, tienda="walmart", search_query="liquidacion", store_id=None):
        """
        Consulta la API de GraphQL de Walmart/Bodega Aurrera usando emulación TLS
        para evitar el error HTTP 412.
        """
        url = self.walmart_api
        default_store = STORES_FALLBACK["WALMART_STORE_ID"] if tienda == "walmart" else STORES_FALLBACK["BODEGA_STORE_ID"]
        final_store_id = store_id or getattr(self.context, f"{tienda.upper()}_STORE_ID", default_store)

        payload = {
            "query": "query SearchAndFilter($searchQuery: String!, $facetFilters: String, $page: Int, $size: Int, $storeId: String!) { search(query: $searchQuery, facetFilters: $facetFilters, page: $page, size: $size, storeId: $storeId) { products { id name brand canonicalUrl priceInfo { currentPrice { price } wasPrice { price } } } } }",
            "variables": {
                "searchQuery": search_query, 
                "facetFilters": "[]", 
                "page": 1, 
                "size": 50, 
                "storeId": str(final_store_id)
            }
        }
        
        headers = {
            "Accept": "application/json", 
            "Content-Type": "application/json",
            "Origin": f"https://www.{tienda}.com.mx", 
            "Referer": f"https://www.{tienda}.com.mx/search?q={search_query}"
        }
        
        try:
            logger.info(f"[{tienda.upper()}] Consultando GraphQL con Store ID: {final_store_id}...")
            response = self.session.post(url, json=payload, headers=headers, timeout=20.0)
            if response.status_code == 200: 
                return response.json()
            else:
                logger.error(f"[{tienda.upper()}] Error API GraphQL del servidor: {response.status_code}")
        except Exception as e: 
            logger.error(f"[{tienda.upper()}] Excepción de red en consulta GraphQL: {str(e)}")
        return {}

    def fetch_chedraui_vtex(self):
        """
        Consulta la API VTEX de Chedraui ordenando por el mayor descuento disponible.
        """
        params = {"_from": "0", "_to": "49", "O": "OrderByBestDiscountDESC"}
        try:
            logger.info("[CHEDRAUI] Consultando catálogo VTEX directo...")
            response = self.session.get(self.chedraui_api, params=params, timeout=20.0)
            if response.status_code == 200: 
                return response.json()
        except Exception as e: 
            logger.error(f"[CHEDRAUI] Error de comunicación VTEX: {str(e)}")
        return []

    def fetch_mercado_libre_api(self, query="liquidacion"):
        """
        Consulta la API de Mercado Libre mitigando el error 403 con User-Agent móvil.
        """
        params = {"q": query, "limit": "50", "sort": "discount_desc"}
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
        }
        try:
            logger.info(f"[MERCADO LIBRE] Consultando API pública para: {query}...")
            response = self.session.get(self.mercadolibre_api, params=params, headers=headers, timeout=15.0)
            if response.status_code == 200: 
                return response.json()
        except Exception as e: 
            logger.error(f"[MERCADO LIBRE] Error de comunicación en API: {str(e)}")
        return {}
