"""Cliente GraphQL configurable para Bodega Aurrera."""

from __future__ import annotations

import os
import random
import time

import requests

from core.source_resilience import BLOCKING_STATES, classify_status

from config.walmart_graphql_query import WALMART_GRAPHQL_QUERY
from scrapers.bodega_graphql import BodegaGraphQLParser


LAST_STATUS: dict = {"state": "not_run"}


def consultar_liquidaciones_bodega_api(termino_busqueda: str) -> dict:
    endpoint = os.getenv("BODEGA_GRAPHQL_URL", "").strip()
    store_id = os.getenv("BODEGA_STORE_ID", "").strip() or "0000003001"  # una variable vacía no debe anular el valor por defecto
    if not endpoint:
        LAST_STATUS["state"] = "not_configured"
        return {}

    payload = {
        "query": os.getenv("BODEGA_GRAPHQL_QUERY", "").strip() or WALMART_GRAPHQL_QUERY,
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
            "DexterH4ck-ofertas/2.0 (+deal-monitor; es-MX)",
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
            LAST_STATUS["state"] = classify_status(response.status_code)
            print(f"[Bodega GraphQL] HTTP {response.status_code} ({LAST_STATUS['state']})")
            return {}
        data = response.json()
        LAST_STATUS["state"] = "ok"
        return data
    except requests.Timeout as exc:
        LAST_STATUS["state"] = "timeout"
        print(f"[Bodega GraphQL] timeout: {exc}")
        return {}
    except ValueError as exc:
        LAST_STATUS["state"] = "invalid_response"
        print(f"[Bodega GraphQL] respuesta no JSON: {exc}")
        return {}
    except requests.RequestException as exc:
        LAST_STATUS["state"] = "network_error"
        print(f"[Bodega GraphQL] error: {type(exc).__name__}: {exc}")
        return {}


def buscar_bodega_graphql(terminos=None, marcas_prioritarias=None) -> list[dict]:
    parser = BodegaGraphQLParser(marcas_prioritarias=marcas_prioritarias)
    resultados = []
    for termino in terminos or ("liquidacion", "remate", "oferta", "outlet"):
        payload = consultar_liquidaciones_bodega_api(termino)
        resultados.extend(parser.parsear_respuesta_busqueda(payload))
        # Fuente bloqueada/no disponible: no se insiste con más términos en este ciclo.
        if LAST_STATUS.get("state") in BLOCKING_STATES or LAST_STATUS.get("state") in ("not_configured", "unauthorized", "not_found"):
            break
    return resultados
