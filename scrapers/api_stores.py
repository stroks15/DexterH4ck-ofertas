"""Adaptadores API-first para las 8 tiendas objetivo.

Importante: se usan interfaces públicas/oficiales o endpoints configurables.
No se incluyen técnicas para saltar CAPTCHA/WAF, firmar requests de una app
sin autorización o falsificar credenciales móviles.

Cuando una tienda cambia su API, el endpoint puede sustituirse mediante ENV sin
romper el resto del monitor.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from core.scraper_base import BaseScraper, ScraperContext, run_scrapers_parallel


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


class ChedrauiVtexScraper(BaseScraper):
    store = "Chedraui"

    def discover(self) -> list[dict[str, Any]]:
        # Chedraui expone una búsqueda VTEX; O=OrderByBestDiscountDESC evita
        # descargar primero páginas visuales y prioriza los mayores descuentos.
        base = os.getenv(
            "CHEDRAUI_VTEX_ENDPOINT",
            "https://www.chedraui.com.mx/api/catalog_system/pub/products/search",
        )
        params = {
            "_from": "0",
            "_to": os.getenv("CHEDRAUI_API_TO", "49"),
            "O": "OrderByBestDiscountDESC",
        }
        response = self.get(f"{base}?{urlencode(params)}")
        if response.status_code >= 400:
            print(f"[SCRAPER:Chedraui] VTEX HTTP {response.status_code}")
            return []
        data = response.json()
        output: list[dict[str, Any]] = []
        for product in data if isinstance(data, list) else []:
            items = product.get("items") or []
            item = items[0] if items else {}
            seller = (item.get("sellers") or [{}])[0]
            offer = seller.get("commertialOffer") or {}
            price = _num(offer.get("Price"))
            previous = _num(offer.get("ListPrice"))
            link = product.get("link")
            if not link:
                slug = product.get("linkText") or product.get("productName")
                if slug:
                    link = f"https://www.chedraui.com.mx/p/{slug}"
            if price and link:
                output.append(self.normalize(
                    title=product.get("productName") or product.get("productTitle"),
                    url=link,
                    price=price,
                    previous_price=previous if previous and previous > price else None,
                    marca=product.get("brand"),
                    categoria=product.get("categories", [""])[-1] if product.get("categories") else "",
                    api="vtex",
                ))
        return output


class MercadoLibreApiScraper(BaseScraper):
    store = "Mercado Libre MX"

    def discover(self) -> list[dict[str, Any]]:
        queries = os.getenv(
            "ML_QUERIES",
            "liquidacion,remate,oferta,precio error,descuento",
        ).split(",")
        output: list[dict[str, Any]] = []
        for query in (q.strip() for q in queries if q.strip()):
            params = {"q": query, "limit": "50", "offset": "0"}
            response = self.get(
                "https://api.mercadolibre.com/sites/MLM/search?" + urlencode(params)
            )
            if response.status_code >= 400:
                print(f"[SCRAPER:Mercado Libre MX] API HTTP {response.status_code} q={query!r}")
                continue
            data = response.json()
            for item in data.get("results", []):
                price = _num(item.get("price"))
                if not price:
                    continue
                # La API pública da el precio actual. La referencia histórica se
                # obtiene después desde historial_ofertas.json/Supabase.
                output.append(self.normalize(
                    title=item.get("title"),
                    url=item.get("permalink") or "",
                    price=price,
                    previous_price=None,
                    categoria=item.get("category_id", ""),
                    api="mercadolibre_public",
                ))
        return output


class ConfigurableJsonScraper(BaseScraper):
    """Adaptador para APIs JSON autorizadas/configuradas por ENV."""

    def __init__(self, store: str, endpoint_env: str, context: ScraperContext | None = None):
        super().__init__(context)
        self.store = store
        self.endpoint_env = endpoint_env

    def discover(self) -> list[dict[str, Any]]:
        endpoint = os.getenv(self.endpoint_env, "").strip()
        if not endpoint:
            return []
        response = self.get(endpoint)
        if response.status_code >= 400:
            print(f"[SCRAPER:{self.store}] {self.endpoint_env} HTTP {response.status_code}")
            return []
        data = response.json()
        rows = data if isinstance(data, list) else data.get("results", data.get("products", []))
        output = []
        for row in rows or []:
            price = _num(row.get("price") or row.get("Price") or row.get("precio"))
            previous = _num(
                row.get("compareAtPrice") or row.get("ListPrice") or
                row.get("previous_price") or row.get("precio_anterior")
            )
            url = row.get("url") or row.get("link") or row.get("permalink")
            if price and url:
                output.append(self.normalize(
                    title=row.get("title") or row.get("name") or row.get("productName"),
                    url=url,
                    price=price,
                    previous_price=previous,
                    api="configured_json",
                ))
        return output


def api_first_scrapers() -> list[BaseScraper]:
    """Construye los 8 adaptadores; los no públicos quedan configurables."""
    return [
        ConfigurableJsonScraper("Walmart MX", "WALMART_API_ENDPOINT"),
        ConfigurableJsonScraper("Bodega Aurrera", "BODEGA_API_ENDPOINT"),
        ChedrauiVtexScraper(),
        ConfigurableJsonScraper("Soriana", "SORIANA_API_ENDPOINT"),
        ConfigurableJsonScraper("Coppel", "COPPEL_API_ENDPOINT"),
        ConfigurableJsonScraper("Suburbia", "SUBURBIA_API_ENDPOINT"),
        MercadoLibreApiScraper(),
        ConfigurableJsonScraper("Amazon MX", "AMAZON_API_ENDPOINT"),
    ]


def buscar_api_first() -> list[dict[str, Any]]:
    return run_scrapers_parallel(api_first_scrapers())
