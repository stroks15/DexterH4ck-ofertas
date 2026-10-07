# config/endpoints.py
"""Puntos de integración de tiendas.

Estos valores son dominios/gateways de referencia, no se tratan como endpoints
GraphQL/REST internos. Los endpoints API reales deben configurarse mediante
variables de entorno cuando el comercio los documente o los permita.
"""

ENDPOINTS_REALES = {
    "WALMART_GRAPHQL": "https://walmart.com.mx",
    "BODEGA_GRAPHQL": "https://walmart.com.mx",
    "CHEDRAUI_VTEX": "https://chedraui.com.mx",
    "MERCADOLIBRE_API": "https://mercadolibre.com",
    "COPPEL_API": "https://coppel.com",
    "SUBURBIA_API": "https://suburbia.com.mx",
}

STORES_FALLBACK = {
    "WALMART_STORE_ID": "0000003362",
    "BODEGA_STORE_ID": "0000003001",
}
