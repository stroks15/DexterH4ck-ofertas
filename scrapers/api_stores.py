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
import os
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import requests

from config.endpoints import ENDPOINT_ENV_VARS, get_endpoint
from core.network_json import extract_products
from core.scraper_base import BaseScraper, ScraperContext, run_scrapers_parallel
from core.source_resilience import classify_status
from scrapers.discovery_utils import ESTADOS_CORTE, TERMINOS_LIQUIDACION, env_int, env_terminos

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

    def _urls(self) -> list[str]:
        """Expande {q} (términos de liquidación) y {page}; sin marcadores, una sola URL."""
        if "{q}" not in self.endpoint and "{page}" not in self.endpoint:
            return [self.endpoint]
        prefijo = self.endpoint_name.split("_")[0]
        terminos = env_terminos(f"{prefijo}_QUERIES", TERMINOS_LIQUIDACION) if "{q}" in self.endpoint else ("",)
        paginas = env_int(f"{prefijo}_PAGES", 2, 1, 5) if "{page}" in self.endpoint else 1
        return [
            self.endpoint.replace("{q}", quote_plus(t)).replace("{page}", str(p))
            for t in terminos
            for p in range(1, paginas + 1)
        ]

    def _pedir(self, url: str):
        """(estado, payload). Nunca lanza."""
        try:
            response = self.get(url)
        except requests.Timeout:
            return "timeout", None, None
        except requests.RequestException as exc:
            return "network_error", None, type(exc).__name__
        state = classify_status(response.status_code)
        if state != "ok":
            logger.warning("[%s] API respondió HTTP %s (%s).", self.store, response.status_code, state)
            return state, None, response.status_code
        try:
            return "ok", response.json(), None
        except ValueError:
            return "invalid_response", None, None

    def discover(self) -> list[dict[str, Any]]:
        if not self.endpoint:
            self.last_status = {
                "state": "not_configured",
                "detail": f"{ENDPOINT_ENV_VARS.get(self.endpoint_name, 'endpoint')} no configurado",
            }
            logger.info(
                "[%s] %s no configurado; fuente API omitida.", self.store, ENDPOINT_ENV_VARS.get(self.endpoint_name)
            )
            return []
        rows: list[dict[str, Any]] = []
        vistos: set = set()
        ultimo: dict[str, Any] | None = None
        for url in self._urls():
            estado, payload, extra = self._pedir(url)
            if estado != "ok":
                ultimo = {"state": estado}
                if isinstance(extra, int):
                    ultimo["http"] = extra
                if estado in ESTADOS_CORTE:  # bloqueo/credencial/endpoint: no se insiste
                    break
                continue
            for row in extract_products(payload, self.home or self.endpoint, self.store):
                clave = row.get("id") or row.get("url")
                if clave in vistos:
                    continue
                vistos.add(clave)
                row["origen_link"] = "api_publica"
                rows.append(row)
        if rows:
            self.last_status = {"state": "ok", "count": len(rows)}
        else:
            self.last_status = ultimo or {"state": "ok", "count": 0}
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
            logger.info(
                "[Mercado Libre MX] MERCADOLIBRE_ACCESS_TOKEN no configurado; " "se usa búsqueda pública + historial."
            )
        api = MercadoLibreApi()
        rows = api.discover()
        if api.circuit.paused:
            ultimo = list(api.circuit.failures)[-1] if api.circuit.failures else None
            self.last_status = {"state": classify_status(ultimo), "http": ultimo, "detail": api.circuit.reason}
        else:
            self.last_status = {"state": "ok", "count": len(rows)}
        return rows


class VtexCatalogAdapter(BaseScraper):
    """Adaptador del catálogo público de VTEX, habilitado solo con endpoint explícito."""

    store = "VTEX"
    endpoint_env = ""
    home = ""
    PAGE_SIZE = 50

    def __init__(self, context: ScraperContext | None = None, endpoint: str | None = None):
        super().__init__(context)
        self.endpoint = str(endpoint if endpoint is not None else os.environ.get(self.endpoint_env, "")).strip()
        self.last_status: dict[str, Any] = {"state": "not_run"}

    def _search_url(self) -> str:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        if "/api/catalog_system/pub/products/search" in parsed.path:
            return self.endpoint
        return self.endpoint.rstrip("/") + "/api/catalog_system/pub/products/search"

    def _queries(self) -> list[str]:
        prefix = self.endpoint_env.removesuffix("_VTEX_ENDPOINT")
        raw = os.environ.get(f"{prefix}_QUERIES", "").strip()
        return [q.strip() for q in raw.split("|") if q.strip()] if raw else list(TERMINOS_LIQUIDACION[:3])

    @staticmethod
    def _positive_price(value: Any) -> float | None:
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        return price if price > 0 else None

    def _prices(self, product: dict) -> tuple[float | None, float | None]:
        best = None
        for sku in product.get("items") or []:
            if not isinstance(sku, dict):
                continue
            for seller in sku.get("sellers") or []:
                if not isinstance(seller, dict):
                    continue
                offer = seller.get("commertialOffer") or {}
                if not isinstance(offer, dict):
                    continue
                try:
                    available = float(offer.get("AvailableQuantity") or 0)
                except (TypeError, ValueError):
                    available = 0
                if available <= 0:
                    continue
                price = self._positive_price(offer.get("Price"))
                if price is None:
                    continue
                reference = None
                for field in ("ListPrice", "PriceWithoutDiscount"):
                    candidate = self._positive_price(offer.get(field))
                    if candidate is not None and candidate > price:
                        reference = candidate
                        break
                discount = (1 - price / reference) if reference else 0
                ranking = (discount, -price)
                if best is None or ranking > best[0]:
                    best = (ranking, price, reference)
        return (best[1], best[2]) if best else (None, None)

    def _normalize_product(self, product: dict) -> dict | None:
        price, reference = self._prices(product)
        if price is None:
            return None
        name = str(product.get("productName") or product.get("productTitle") or "").strip()
        link = str(product.get("link") or "").strip()
        if not name or not link:
            return None
        url = urljoin(self.home.rstrip("/") + "/", link)
        row = self.normalize(
            title=name,
            url=url,
            price=price,
            previous_price=reference,
            id=str(product.get("productId") or product.get("productReference") or "") or None,
            origen_link="vtex_catalogo_publico",
        )
        row["descuento"] = round((1 - price / reference) * 100) if reference and reference > price else 0
        return row

    def discover(self) -> list[dict[str, Any]]:
        if not self.endpoint:
            self.last_status = {"state": "not_configured", "detail": f"{self.endpoint_env} no configurado"}
            return []
        endpoint = self._search_url()
        if not endpoint:
            self.last_status = {"state": "invalid_endpoint", "count": 0}
            return []
        prefix = self.endpoint_env.removesuffix("_VTEX_ENDPOINT")
        try:
            pages = max(1, min(5, int(os.environ.get(f"{prefix}_VTEX_PAGES", "1"))))
        except (TypeError, ValueError):
            pages = 1

        products: dict[str, dict] = {}
        errors: list[str] = []
        for query in self._queries():
            for page in range(pages):
                try:
                    response = self.get(
                        endpoint,
                        params={"ft": query, "_from": page * self.PAGE_SIZE, "_to": (page + 1) * self.PAGE_SIZE - 1},
                    )
                except requests.Timeout:
                    errors.append("timeout")
                    break
                except requests.RequestException as exc:
                    errors.append("network_error")
                    logger.warning("[%s] VTEX error de red (%s).", self.store, type(exc).__name__)
                    break

                state = classify_status(response.status_code)
                if state != "ok":
                    errors.append(state)
                    logger.warning("[%s] VTEX HTTP %s (%s).", self.store, response.status_code, state)
                    if response.status_code in (401, 403, 404, 429, 451):
                        break
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    errors.append("invalid_response")
                    break
                if not isinstance(payload, list):
                    errors.append("invalid_response")
                    break
                for product in payload:
                    if isinstance(product, dict):
                        key = str(product.get("productId") or product.get("link") or product.get("productName") or "")
                        if key:
                            products.setdefault(key, product)
                if len(payload) < self.PAGE_SIZE:
                    break

        rows = []
        for product in products.values():
            try:
                row = self._normalize_product(product)
                if row:
                    rows.append(row)
            except (TypeError, ValueError, KeyError) as exc:
                logger.debug("[%s] Producto VTEX omitido (%s).", self.store, type(exc).__name__)

        if rows:
            self.last_status = {"state": "ok", "count": len(rows)}
        elif errors:
            self.last_status = {"state": errors[-1], "count": 0}
        else:
            self.last_status = {"state": "empty", "count": 0}
        return rows


class CoppelVtexAdapter(VtexCatalogAdapter):
    store = "Coppel"
    endpoint_env = "COPPEL_VTEX_ENDPOINT"
    home = "https://www.coppel.com"


class LiverpoolVtexAdapter(VtexCatalogAdapter):
    store = "Liverpool"
    endpoint_env = "LIVERPOOL_VTEX_ENDPOINT"
    home = "https://www.liverpool.com.mx"


def _adapter(name: str, endpoint_name: str, home: str):
    return type(
        f"{name}Adapter",
        (JsonEndpointAdapter,),
        {
            "store": name,
            "endpoint_name": endpoint_name,
            "home": home,
        },
    )


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
    liverpool = (
        LiverpoolVtexAdapter(context)
        if os.environ.get("LIVERPOOL_VTEX_ENDPOINT", "").strip()
        else LiverpoolAdapter(context)
    )
    coppel = (
        CoppelVtexAdapter(context)
        if os.environ.get("COPPEL_VTEX_ENDPOINT", "").strip()
        else CoppelAdapter(context)
    )
    return [
        WalmartAdapter(context),
        BodegaAdapter(context),
        chedraui,
        SorianaAdapter(context),
        liverpool,
        AmazonAdapter(context),
        MercadoLibreAdapter(context),
        coppel,
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
