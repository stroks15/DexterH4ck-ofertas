"""Cliente GraphQL configurable para Bodega Aurrera."""

from __future__ import annotations

import os
import random
import time

import requests

from config.walmart_graphql_query import WALMART_GRAPHQL_QUERY
from scrapers.bodega_graphql import BodegaGraphQLParser


def consultar_liquidaciones_bodega_api(termino_busqueda: str) -> dict:
    endpoint = os.getenv("BODEGA_GRAPHQL_URL", "").strip()
    store_id = os.getenv("BODEGA_STORE_ID", "0000003001").strip()
    if not endpoint:
        return {}

    payload = {
        "query": WALMART_GRAPHQL_QUERY,
        "variables": {
            "searchQuery": termino_busqueda,
            "facetFilters": "[]",
            "page": 1,
            "size": 40,
            "storeId": store_id,
        },
    }
    headers = {
        "User-Agent": os.getenv(
            "SCRAPER_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.bodegaaurrera.com.mx",
        "Referer": "https://www.bodegaaurrera.com.mx/",
        "X-Apollo-Operation-Name": "SearchAndFilter",
    }
    time.sleep(random.uniform(2.5, 4.5))
    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"[Bodega GraphQL] HTTP {response.status_code}")
            return {}
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[Bodega GraphQL] error: {type(exc).__name__}: {exc}")
        return {}


def buscar_bodega_graphql(terminos=None, marcas_prioritarias=None) -> list[dict]:
    parser = BodegaGraphQLParser(marcas_prioritarias=marcas_prioritarias)
    resultados = []
    for termino in terminos or ("liquidacion", "remate", "oferta", "outlet"):
        payload = consultar_liquidaciones_bodega_api(termino)
        resultados.extend(parser.parsear_respuesta_busqueda(payload))
    return resultados
