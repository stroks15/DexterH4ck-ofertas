# scrapers/api_stores.py
"""Capa API-first: un adaptador aislado por tienda.

Cada adaptador implementa `discover()` y expone `last_status` con el estado REAL de
la fuente (ok, not_configured, forbidden, rate_limited, timeout, invalid_response...).
Un bloqueo o error de una tienda nunca se convierte en "success" y nunca detiene a las
demás. Los endpoints se configuran por variables de entorno; no se hardcodean
endpoints internos y no se intenta evadir CAPTCHA/WAF.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from config.endpoints import ENDPOINT_ENV_VARS, get_endpoint
from core.network_json import extract_products
from core.scraper_base import BaseScraper, ScraperContext, run_scrapers_parallel
from core.source_resilience import classify_status

try:  # La capa VTEX es opcional
    from scrapers.vtex_stores import VtexStoresScraper
except ImportError:  # pragma: no cover
    VtexStoresScraper = None

logger = logging.getLogger("DexterH4ck.ApiStores")


class JsonEndpointAdapter(BaseScraper):
    """Adaptador genérico para un endpoint JSON autorizado configurado por entorno."""

    store = "unknown"
    endpoint_name = ""
    home = ""

    def __init__(self, context: ScraperContext | None = None, endpoint: str | None = None) -> None:
        super().__init__(context)
        self.endpoint = (endpoint if endpoint is not None else get_endpoint(self.endpoint_name)).strip()
        self.last_status: dict[str, Any] = {"state": "not_run"}

    def discover(self) -> list[dict[str, Any]]:
        if not self.endpoint:
            self.last_status = {"state": "not_configured",
                                "detail": f"{ENDPOINT_ENV_VARS.get(self.endpoint_name, 'endpoint')} no configurado"}
            logger.info("[%s] %s no configurado; fuente API omitida.", self.store, ENDPOINT_ENV_VARS.get(self.endpoint_name))
            return []
        try:
            response = self.get(self.endpoint)
        except requests.Timeout:
            self.last_status = {"state": "timeout"}
            return []
        except requests.RequestException as exc:
            self.last_status = {"state": "network_error", "error": type(exc).__name__}
            return []
        state = classify_status(response.status_code)
        if state != "ok":
            self.last_status = {"state": state, "http": response.status_code}
            logger.warning("[%s] API respondió HTTP %s (%s).", self.store, response.status_code, state)
            return []
        try:
            payload = response.json()
        except ValueError:
            self.last_status = {"state": "invalid_response"}
            return []
        rows = extract_products(payload, self.home or self.endpoint, self.store)
        for row in rows:
            row["origen_link"] = "api_publica"
        self.last_status = {"state": "ok", "count": len(rows)}
        return rows


class WalmartAdapter(BaseScraper):
    store = "Walmart MX"

    def __init__(self, context: ScraperContext | None = None) -> None:
        super().__init__(context)
        self.last_status: dict[str, Any] = {"state": "not_run"}

    def discover(self) -> list[dict[str, Any]]:
        from scrapers import walmart_api
        rows = walmart_api.buscar_walmart_graphql()
        self.last_status = dict(walmart_api.LAST_STATUS)
        if self.last_status.get("state") == "ok":
            self.last_status["count"] = len(rows)
        return rows


class BodegaAdapter(BaseScraper):
    store = "Bodega Aurrera"

    def __init__(self, context: ScraperContext | None = None) -> None:
        super().__init__(context)
        self.last_status: dict[str, Any] = {"state": "not_run"}

    def discover(self) -> list[dict[str, Any]]:
        from scrapers import bodega_aurrera_api
        rows = bodega_aurrera_api.buscar_bodega_graphql()
        self.last_status = dict(bodega_aurrera_api.LAST_STATUS)
        if self.last_status.get("state") == "ok":
            self.last_status["count"] = len(rows)
        return rows


class MercadoLibreAdapter(BaseScraper):
    """API pública de Mercado Libre; MERCADOLIBRE_ACCESS_TOKEN es opcional."""

    store = "Mercado Libre MX"

    def __init__(self, context: ScraperContext | None = None) -> None:
        super().__init__(context)
        self.last_status: dict[str, Any] = {"state": "not_run"}

    def discover(self) -> list[dict[str, Any]]:
        import os
        from scrapers.mercado_libre_api import MercadoLibreApi
        if not os.getenv("MERCADOLIBRE_ACCESS_TOKEN", "").strip():
            logger.info("[Mercado Libre MX] MERCADOLIBRE_ACCESS_TOKEN no configurado; "
                        "se usa búsqueda pública + historial.")
        api = MercadoLibreApi()
        rows = api.discover()
        if api.circuit.paused:
            ultimo = list(api.circuit.failures)[-1] if api.circuit.failures else None
            self.last_status = {"state": classify_status(ultimo), "http": ultimo, "detail": api.circuit.reason}
        else:
            self.last_status = {"state": "ok", "count": len(rows)}
        return rows


def _adapter(name: str, endpoint_name: str, home: str):
    return type(f"{name}Adapter", (JsonEndpointAdapter,), {
        "store": name, "endpoint_name": endpoint_name, "home": home,
    })


SorianaAdapter = _adapter("Soriana", "SORIANA_API", "https://www.soriana.com")
LiverpoolAdapter = _adapter("Liverpool", "LIVERPOOL_API", "https://www.liverpool.com.mx")
AmazonAdapter = _adapter("Amazon MX", "AMAZON_API", "https://www.amazon.com.mx")
CoppelAdapter = _adapter("Coppel", "COPPEL_API", "https://www.coppel.com")
SuburbiaAdapter = _adapter("Suburbia", "SUBURBIA_API", "https://www.suburbia.com.mx")
OferstockAdapter = _adapter("Oferstock", "OFERSTOCK_API", "https://www.oferstock.com.mx")
ChedrauiJsonAdapter = _adapter("Chedraui", "CHEDRAUI_VTEX", "https://www.chedraui.com.mx")


def api_first_scrapers(context: ScraperContext | None = None) -> list[BaseScraper]:
    """Un adaptador independiente por cada tienda objetivo."""
    chedraui = VtexStoresScraper(context) if VtexStoresScraper else ChedrauiJsonAdapter(context)
    return [
        WalmartAdapter(context),
        BodegaAdapter(context),
        chedraui,
        SorianaAdapter(context),
        LiverpoolAdapter(context),
        AmazonAdapter(context),
        MercadoLibreAdapter(context),
        CoppelAdapter(context),
        SuburbiaAdapter(context),
        OferstockAdapter(context),
    ]


class ApiStoresScraper:
    """Fachada API-first: agrupa los adaptadores aislados por tienda."""

    def __init__(self, context: ScraperContext | None = None) -> None:
        self.context = context
        self.scrapers = api_first_scrapers(context)

    def discover(self) -> list[dict[str, Any]]:
        return run_scrapers_parallel(self.scrapers)
