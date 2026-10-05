"""Cliente GraphQL configurable para Walmart MX."""

from __future__ import annotations

import os
import random
import time

import requests

from config.walmart_graphql_query import WALMART_GRAPHQL_QUERY
from scrapers.walmart_graphql import WalmartGraphQLParser


def consultar_liquidaciones_walmart_api(termino_busqueda: str) -> dict:
    endpoint = os.getenv("WALMART_GRAPHQL_URL", "").strip()
    store_id = os.getenv("WALMART_STORE_ID", "0000003362").strip()
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
        "Origin": "https://www.walmart.com.mx",
        "Referer": "https://www.walmart.com.mx/",
        "X-Apollo-Operation-Name": "SearchAndFilter",
    }
    time.sleep(random.uniform(2.5, 4.5))
    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"[Walmart GraphQL] HTTP {response.status_code}")
            return {}
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[Walmart GraphQL] error: {type(exc).__name__}: {exc}")
        return {}


def buscar_walmart_graphql(terminos=None, marcas_prioritarias=None) -> list[dict]:
    parser = WalmartGraphQLParser(marcas_prioritarias=marcas_prioritarias)
    resultados = []
    for termino in terminos or ("liquidacion", "remate", "oferta", "outlet"):
        payload = consultar_liquidaciones_walmart_api(termino)
        resultados.extend(parser.parsear_respuesta_busqueda(payload))
    return resultados
