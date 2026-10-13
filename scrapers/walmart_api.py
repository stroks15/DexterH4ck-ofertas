"""Cliente GraphQL configurable para Walmart MX."""

from __future__ import annotations

import os
import random
import time

import requests

from core.source_resilience import classify_status

from config.endpoints import get_store_id
from config.walmart_graphql_query import WALMART_GRAPHQL_QUERY
from scrapers.discovery_utils import TERMINOS_LIQUIDACION, buscar_graphql_paginado, env_int, env_terminos
from scrapers.walmart_graphql import WalmartGraphQLParser


LAST_STATUS: dict = {"state": "not_run"}


def consultar_liquidaciones_walmart_api(termino_busqueda: str, pagina: int = 1, tamano: int = 40) -> dict:
    endpoint = os.getenv("WALMART_GRAPHQL_URL", "").strip()
    store_id = get_store_id("WALMART")  # una variable vacía no debe anular el valor por defecto (2033)
    if not endpoint:
        LAST_STATUS["state"] = "not_configured"
        return {}

    payload = {
        "query": os.getenv("WALMART_GRAPHQL_QUERY", "").strip() or WALMART_GRAPHQL_QUERY,
        "variables": {
            "searchQuery": termino_busqueda,
            "facetFilters": "[]",
            "page": pagina,
            "size": tamano,
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
        "Origin": "https://www.walmart.com.mx",
        "Referer": "https://www.walmart.com.mx/",
        "X-Apollo-Operation-Name": "SearchAndFilter",
        "Tenant": os.getenv("WALMART_TENANT", "").strip() or "WM_MEXICO",
    }
    time.sleep(random.uniform(2.5, 4.5))
    try:
        # Sin seguir redirecciones: un 3xx hacia telemetría/publicidad no es una respuesta del catálogo.
        response = requests.post(endpoint, json=payload, headers=headers, timeout=15, allow_redirects=False)
        if 300 <= response.status_code < 400:
            LAST_STATUS["state"] = "redirect"
            print(f"[Walmart GraphQL] HTTP {response.status_code}: redirección ignorada (endpoint inválido o bloqueo).")
            return {}
        if response.status_code != 200:
            LAST_STATUS["state"] = classify_status(response.status_code)
            print(f"[Walmart GraphQL] HTTP {response.status_code} ({LAST_STATUS['state']})")
            return {}
        data = response.json()
        LAST_STATUS["state"] = "ok"
        return data
    except requests.Timeout as exc:
        LAST_STATUS["state"] = "timeout"
        print(f"[Walmart GraphQL] timeout: {exc}")
        return {}
    except ValueError as exc:
        LAST_STATUS["state"] = "invalid_response"
        print(f"[Walmart GraphQL] respuesta no JSON: {exc}")
        return {}
    except requests.RequestException as exc:
        LAST_STATUS["state"] = "network_error"
        print(f"[Walmart GraphQL] error: {type(exc).__name__}: {exc}")
        return {}


def buscar_walmart_graphql(terminos=None, marcas_prioritarias=None) -> list[dict]:
    """Busca liquidaciones por varios términos y páginas (WALMART_QUERIES/BODEGA_QUERIES y *_PAGES)."""
    parser = WalmartGraphQLParser(marcas_prioritarias=marcas_prioritarias)
    terminos = tuple(terminos) if terminos else env_terminos("WALMART_QUERIES", TERMINOS_LIQUIDACION)
    return buscar_graphql_paginado(
        consultar_liquidaciones_walmart_api, parser, terminos,
        paginas=env_int("WALMART_PAGES", 2, 1, 5), tamano=40, estado=LAST_STATUS,
    )
