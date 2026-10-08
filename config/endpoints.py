# config/endpoints.py
"""Endpoints de APIs de tiendas.

NO se hardcodean endpoints internos/no documentados. Cada endpoint se configura por
variable de entorno (opcional). Si una variable no existe, el valor es "" y la
fuente correspondiente se reporta como `not_configured` sin afectar a las demás.
"""
import os

# nombre lógico -> variable de entorno
ENDPOINT_ENV_VARS = {
    "WALMART_GRAPHQL": "WALMART_GRAPHQL_URL",
    "BODEGA_GRAPHQL": "BODEGA_GRAPHQL_URL",
    "CHEDRAUI_VTEX": "CHEDRAUI_VTEX_ENDPOINT",
    "SORIANA_API": "SORIANA_API_ENDPOINT",
    "COPPEL_API": "COPPEL_API_ENDPOINT",
    "SUBURBIA_API": "SUBURBIA_API_ENDPOINT",
    "AMAZON_API": "AMAZON_API_ENDPOINT",
    "LIVERPOOL_API": "LIVERPOOL_API_ENDPOINT",
    "OFERSTOCK_API": "OFERSTOCK_API_ENDPOINT",
}

# API pública y documentada de Mercado Libre (el token es opcional).
MERCADOLIBRE_PUBLIC_API = "https://api.mercadolibre.com"

STORES_FALLBACK = {
    "WALMART_STORE_ID": "0000003362",
    "BODEGA_STORE_ID": "0000003001",
}


def get_endpoint(name: str) -> str:
    """Endpoint configurado o "" si no existe (nunca lanza excepción)."""
    return os.environ.get(ENDPOINT_ENV_VARS.get(name, ""), "").strip()


def get_store_id(tienda: str) -> str:
    key = f"{tienda.upper()}_STORE_ID"
    return os.environ.get(key, "").strip() or STORES_FALLBACK.get(key, "")


# Compatibilidad con el nombre anterior del diccionario.
ENDPOINTS_REALES = {name: get_endpoint(name) for name in ENDPOINT_ENV_VARS}
ENDPOINTS_REALES["MERCADOLIBRE_API"] = MERCADOLIBRE_PUBLIC_API
