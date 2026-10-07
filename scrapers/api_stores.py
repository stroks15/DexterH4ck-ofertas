"""Adaptadores API-first para las 8 tiendas objetivo.

Importante: se usan interfaces públicas/oficiales o endpoints configurables.
No se incluyen técnicas para saltar CAPTCHA/WAF, firmar requests de una app
sin autorización o falsificar credenciales móviles.

Cuando una tienda cambia su API, el endpoint puede sustituirse mediante ENV sin
romper el resto del monitor.
"""

from __future__ import annotations

import os
import time
from typing import Any

from curl_cffi import requests as curl_requests

from config.endpoints import ENDPOINTS_REALES, STORES_FALLBACK
from urllib.parse import urlencode

import requests

from core.scraper_base import BaseScraper, ScraperContext, run_scrapers_parallel
from core.source_resilience import SourceCircuit
from core.network_json import extract_products


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


class GraphQLStoreScraper(BaseScraper):
    """Cliente GraphQL configurable para catálogos autorizados.

    STORE_ID se inyecta como variable y header únicamente cuando el comercio
    documenta/permite ese contexto. No se intenta descubrir ni falsificar
    cookies de una app móvil.
    """

    def __init__(self, store: str, endpoint_env: str, store_id_env: str, query_env: str,
                 context: ScraperContext | None = None):
        super().__init__(context)
        self.store = store
        self.endpoint_env = endpoint_env
        self.store_id_env = store_id_env
        self.query_env = query_env

    def discover(self) -> list[dict[str, Any]]:
        import json
        endpoint = os.getenv(self.endpoint_env, "").strip()
        query = os.getenv(self.query_env, "").strip()
        if not endpoint or not query:
            return []
        store_id = os.getenv(self.store_id_env, "").strip()
        variables = {"storeId": store_id} if store_id else {}
        headers = {"Content-Type": "application/json"}
        if store_id:
            headers["X-Store-Id"] = store_id
        response = self.session.post(
            endpoint,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=self.context.timeout,
        )
        if response.status_code >= 400:
            print(f"[SCRAPER:{self.store}] GraphQL HTTP {response.status_code}")
            return []
        payload = response.json()
        rows = payload.get("data", {}).get("products", []) if isinstance(payload, dict) else []
        output = []
        for row in rows:
            price = _num(row.get("price") or row.get("currentPrice"))
            previous = _num(row.get("previousPrice") or row.get("listPrice"))
            url = row.get("url") or row.get("link")
            if price and url:
                output.append(self.normalize(
                    title=row.get("title") or row.get("name"),
                    url=url,
                    price=price,
                    previous_price=previous,
                    api="graphql",
                    store_id=store_id,
                ))
        return output


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



class CoppelPublicScraper(BaseScraper):
    store = "Coppel"

    def discover(self) -> list[dict[str, Any]]:
        from scrapers.coppel_public import extract_products_from_next_data, extract_product_from_next_data
        urls = [
            "https://www.coppel.com/ofertas",
            "https://www.coppel.com/ca/outlet-saldos",
            "https://www.coppel.com/l/ofertas",
        ]
        output = []
        for url in urls:
            try:
                response = self.get(
                    url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
                        "Cache-Control": "no-cache",
                    },
                    timeout=18,
                )
                if response.status_code >= 400:
                    print(f"[SCRAPER:Coppel] HTML HTTP {response.status_code} en {url}")
                    continue
                items = extract_products_from_next_data(response.text, response.url)
                output.extend(items or [])
            except requests.RequestException as exc:
                print(f"[SCRAPER:Coppel] {type(exc).__name__}: {exc}")
        return output

class MercadoLibreApiScraper(BaseScraper):
    store = "Mercado Libre MX"

    def _precio_referencia(self, item_id: str):
        token = os.getenv("MERCADOLIBRE_ACCESS_TOKEN", "").strip()
        if not token or not item_id:
            return None
        try:
            response = self.session.get(
                f"https://api.mercadolibre.com/items/{item_id}/prices",
                headers={"Authorization": f"Bearer {token}"},
                params={"display_version": "true"},
                timeout=self.context.timeout,
            )
            if response.status_code >= 400:
                print(f"[SCRAPER:Mercado Libre MX] /prices HTTP {response.status_code} item={item_id}")
                return None
            data = response.json()
            prices = data.get("prices") or []
            current = None
            references = []
            for price in prices:
                amount = _num(price.get("amount"))
                regular = _num(price.get("regular_amount"))
                if price.get("type") == "promotion" and amount:
                    current = amount
                    if regular and regular > amount:
                        references.append(regular)
                elif price.get("type") == "standard" and amount:
                    if current is None:
                        current = amount
            if references:
                return max(references)
            # Sin promoción activa, no inventamos un descuento usando otro
            # precio estándar: el monitor usará su historial como respaldo.
            return None
        except (requests.RequestException, ValueError) as exc:
            print(f"[SCRAPER:Mercado Libre MX] error /prices item={item_id}: {type(exc).__name__}")
            return None

    def discover(self) -> list[dict[str, Any]]:
        circuit = SourceCircuit(self.store)
        token = os.getenv("MERCADOLIBRE_ACCESS_TOKEN", "").strip()
        require_token = os.getenv("ML_API_REQUIRE_TOKEN", "false").lower() not in {"0", "false", "no"}
        if require_token and not token:
            print("[SCRAPER:Mercado Libre MX] API omitida: falta MERCADOLIBRE_ACCESS_TOKEN; se conserva el descubrimiento web/indexado.")
            return []
        queries = os.getenv(
            "ML_QUERIES",
            "liquidacion,remate,oferta,precio error,descuento",
        ).split(",")
        output: list[dict[str, Any]] = []
        max_price_lookups = max(0, int(os.getenv("ML_PRICE_LOOKUPS", "40")))
        lookups = 0
        seen_ids = set()

        for query in (q.strip() for q in queries if q.strip()):
            if not circuit.can_continue():
                break
            params = {"q": query, "limit": "50", "offset": "0"}
            response = self.get(
                "https://api.mercadolibre.com/sites/MLM/search?" + urlencode(params)
            )
            if response.status_code >= 400:
                paused = circuit.record(
                    response.status_code,
                    f"HTTP {response.status_code}",
                )
                print(
                    f"[SCRAPER:Mercado Libre MX] API HTTP {response.status_code}; "
                    f"se conserva el fallback público/indexado. paused={paused}"
                )
                # Un 401/403 no mejora repitiendo la misma llamada anónima.
                # Cortamos la API en este ciclo para que el resto del monitor
                # pueda trabajar con HTML, comunidad e historial.
                if response.status_code in {401, 403, 451}:
                    break
                continue
            data = response.json()
            for item in data.get("results", []):
                price = _num(item.get("price"))
                if not price:
                    continue

                previous = None
                item_id = str(item.get("id") or "")
                if item_id and item_id not in seen_ids and lookups < max_price_lookups:
                    seen_ids.add(item_id)
                    previous = self._precio_referencia(item_id)
                    lookups += 1
                    time.sleep(0.15)

                output.append(self.normalize(
                    title=item.get("title"),
                    url=item.get("permalink") or "",
                    price=price,
                    previous_price=previous,
                    categoria=item.get("category_id", ""),
                    marca=item.get("brand") or "",
                    api="mercadolibre_prices" if previous else "mercadolibre_public",
                    item_id=item_id,
                ))
        print(f"[SCRAPER:Mercado Libre MX] enriquecimiento /prices: {lookups}/{max_price_lookups}")
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
        # Primero usamos el extractor semántico, que tolera respuestas
        # anidadas y variantes de nombres sin depender de una ruta JSON fija.
        output = extract_products(data, response.url, self.store)
        if output:
            return output
        # Fallback legacy para APIs muy simples.
        rows = data if isinstance(data, list) else data.get("results", data.get("products", []))
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            price = _num(row.get("price") or row.get("Price") or row.get("precio"))
            previous = _num(row.get("compareAtPrice") or row.get("ListPrice") or row.get("previous_price") or row.get("precio_anterior"))
            url = row.get("url") or row.get("link") or row.get("permalink")
            if price and url:
                output.append(self.normalize(title=row.get("title") or row.get("name") or row.get("productName"), url=url, price=price, previous_price=previous, api="configured_json"))
        return output


class FunctionScraper(BaseScraper):
    """Adapta un scraper especializado al contrato común de la capa API-first."""

    def __init__(self, store: str, function, context: ScraperContext | None = None):
        super().__init__(context)
        self.store = store
        self.function = function

    def discover(self) -> list[dict[str, Any]]:
        try:
            return self.function() or []
        except Exception as exc:
            print(f"[SCRAPER:{self.store}] adaptador especializado: {type(exc).__name__}: {exc}")
            return []



class ApiStoresScraper:
    """Gateway HTTP para APIs públicas/configuradas.

    No realiza evasión de WAF/CAPTCHA ni suplantación TLS. Los endpoints
    GraphQL de Walmart/Bodega solo se usan cuando están configurados
    explícitamente; si no, el orquestador conserva los scrapers públicos.
    """

    def __init__(self, context: ScraperContext | None = None):
        self.context = context or ScraperContext()
        self.session = curl_requests.Session()
        self.session.headers.update(self.context.headers)
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
        })
        self.gateway_bases = ENDPOINTS_REALES
        self.walmart_api = os.getenv("WALMART_GRAPHQL_URL", "").strip()
        self.bodega_api = os.getenv("BODEGA_GRAPHQL_URL", "").strip()
        self.chedraui_api = (
            os.getenv("CHEDRAUI_VTEX_ENDPOINT", "").strip()
            or "https://www.chedraui.com.mx/api/catalog_system/pub/products/search"
        )
        self.mercadolibre_api = (
            os.getenv("MERCADOLIBRE_API_ENDPOINT", "").strip()
            or "https://api.mercadolibre.com/sites/MLM/search"
        )

    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        if not url:
            return {}
        try:
            response = self.session.post(
                url,
                json=payload,
                headers=headers or {},
                timeout=min(float(self.context.timeout) * 2, 30.0),
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, dict) else {}
            print(f"[SCRAPER:ApiGateway] HTTP {response.status_code} en {url}")
        except Exception as exc:
            print(f"[SCRAPER:ApiGateway] {type(exc).__name__}: {exc}")
        return {}

    def fetch_walmart_bodega_graphql(self, tienda: str = "walmart", search_query: str = "liquidacion", store_id: str | None = None) -> dict[str, Any]:
        tienda_key = tienda.strip().lower()
        if tienda_key not in {"walmart", "bodega", "bodegaaurrera"}:
            return {}
        endpoint = self.walmart_api if tienda_key == "walmart" else self.bodega_api
        if not endpoint:
            return {}
        default_store = STORES_FALLBACK["WALMART_STORE_ID"] if tienda_key == "walmart" else STORES_FALLBACK["BODEGA_STORE_ID"]
        env_name = "WALMART_STORE_ID" if tienda_key == "walmart" else "BODEGA_STORE_ID"
        final_store_id = store_id or os.getenv(env_name, default_store)
        from config.walmart_graphql_query import WALMART_GRAPHQL_QUERY
        payload = {
            "query": WALMART_GRAPHQL_QUERY,
            "variables": {
                "searchQuery": search_query,
                "facetFilters": "[]",
                "page": 1,
                "size": 50,
                "storeId": str(final_store_id),
            },
        }
        domain = "www.walmart.com.mx" if tienda_key == "walmart" else "www.bodegaaurrera.com.mx"
        return self._post_json(
            endpoint,
            payload,
            headers={
                "Content-Type": "application/json",
                "Origin": f"https://{domain}",
                "Referer": f"https://{domain}/search?q={search_query}",
                "X-Apollo-Operation-Name": "SearchAndFilter",
            },
        )

    def fetch_chedraui_vtex(self) -> list[dict[str, Any]]:
        try:
            response = self.session.get(
                self.chedraui_api,
                params={"_from": "0", "_to": "49", "O": "OrderByBestDiscountDESC"},
                timeout=min(float(self.context.timeout) * 2, 30.0),
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, list) else []
        except Exception as exc:
            print(f"[SCRAPER:ApiGateway:Chedraui] {type(exc).__name__}: {exc}")
        return []

    def fetch_mercado_libre_api(self, query: str = "liquidacion") -> dict[str, Any]:
        try:
            response = self.session.get(
                self.mercadolibre_api,
                params={"q": query, "limit": "50", "offset": "0"},
                timeout=min(float(self.context.timeout) * 2, 30.0),
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, dict) else {}
        except Exception as exc:
            print(f"[SCRAPER:ApiGateway:MercadoLibre] {type(exc).__name__}: {exc}")
        return {}

def api_first_scrapers() -> list[BaseScraper]:
    """Construye adaptadores homogéneos y deja cada fuente aislada."""
    from scrapers.bodega_aurrera_api import buscar_bodega_graphql
    from scrapers.walmart_api import buscar_walmart_graphql

    return [
        FunctionScraper("Walmart MX", buscar_walmart_graphql),
        FunctionScraper("Bodega Aurrera", buscar_bodega_graphql),
        ChedrauiVtexScraper(),
        ConfigurableJsonScraper("Soriana", "SORIANA_API_ENDPOINT"),
        CoppelPublicScraper(),
        ConfigurableJsonScraper("Suburbia", "SUBURBIA_API_ENDPOINT"),
        ConfigurableJsonScraper("Liverpool", "LIVERPOOL_API_ENDPOINT"),
        ConfigurableJsonScraper("Oferstock", "OFERSTOCK_API_ENDPOINT"),
        MercadoLibreApiScraper(),
        ConfigurableJsonScraper("Amazon MX", "AMAZON_API_ENDPOINT"),
    ]


def buscar_api_first() -> list[dict[str, Any]]:
    results = run_scrapers_parallel(api_first_scrapers())
    if os.getenv("NETWORK_BROWSER_ENABLED", "false").lower() in {"1", "true", "yes"}:
        try:
            from scrapers.browser_network import buscar_network_browser
            results.extend(buscar_network_browser())
        except Exception as exc:
            print(f"[SCRAPER:NetworkBrowser] ERROR aislado: {type(exc).__name__}: {exc}")
    try:
        from scrapers.vtex_stores import VtexStoresScraper
        vtex = VtexStoresScraper()
        results.extend(vtex.fetch_coppel_liquidations())
        results.extend(vtex.fetch_suburbia_liquidations())
    except Exception as exc:
        print(f"[SCRAPER:VtexStores] ERROR aislado: {type(exc).__name__}: {exc}")
    return results
