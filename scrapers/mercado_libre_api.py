"""Scraper robusto de Mercado Libre México.

Usa la API oficial de búsqueda de Mercado Libre (/sites/MLM/search) y, cuando
hay token, /items/{id}/prices para validar el precio promocional. No intenta
saltar WAF/CAPTCHA ni inventa descuentos.

La API actual permite ordenar/filtrar mediante los valores disponibles para
cada sitio; por eso sort=discount_desc se intenta de forma segura y, si el
sitio lo rechaza, se hace fallback a la búsqueda normal y se calcula el
descuento localmente.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests

from core.source_resilience import SourceCircuit

LOG = logging.getLogger("mercado_libre_api")
API = "https://api.mercadolibre.com"
SITE_ID = "MLM"
MIN_DISCOUNT = 50
MAX_DISCOUNT = 99

DEFAULT_QUERIES = (
    "celulares,laptops,tablets,televisores,consolas,videojuegos,audio,"
    "electrodomesticos,lavadoras,refrigeradores,microondas,colchones,"
    "muebles,ropa,calzado,bebes,pañales,cunas,carriolas,juguetes,"
    "belleza,mascotas,herramientas,hogar"
)


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(str(value).replace("$", "").replace(",", "").strip())
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _discount(current: float | None, reference: float | None) -> int:
    if not current or not reference or reference <= current:
        return 0
    return max(0, min(99, round((1 - current / reference) * 100)))


def _brand(item: dict[str, Any]) -> str:
    if item.get("brand"):
        return str(item["brand"])
    for attr in item.get("attributes") or []:
        if str(attr.get("id", "")).upper() == "BRAND":
            return str(attr.get("value_name") or attr.get("value_id") or "")
    return ""


def _official_store_id(item: dict[str, Any]) -> str | None:
    value = item.get("official_store_id")
    return str(value) if value not in (None, "", 0, "0") else None


class MercadoLibreApi:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.timeout = float(os.getenv("ML_API_TIMEOUT", "12"))
        self.delay = float(os.getenv("ML_API_DELAY", "0.20"))
        self.token = os.getenv("MERCADOLIBRE_ACCESS_TOKEN", "").strip()
        self.official_only = os.getenv("ML_OFFICIAL_ONLY", "false").lower() not in {"0", "false", "no"}
        self.limit = min(max(int(os.getenv("ML_API_LIMIT", "50")), 1), 100)
        self.max_price_lookups = max(int(os.getenv("ML_PRICE_LOOKUPS", "40")), 0)
        self.circuit = SourceCircuit("Mercado Libre MX")

    def _get(self, path: str, params: dict[str, Any]) -> requests.Response | None:
        headers = {
            "Accept": "application/json",
            "User-Agent": "DexterH4ck-ofertas/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.session.get(
                f"{API}{path}",
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                self.circuit.record(response.status_code, f"HTTP {response.status_code}")
                self.circuit.backoff(response.status_code, retry_after=response.headers.get("Retry-After"))
            return response
        except requests.RequestException as exc:
            LOG.warning("[MELI API] %s: %s", type(exc).__name__, exc)
            return None

    def _price_reference(self, item_id: str) -> tuple[float | None, float | None]:
        if not self.token or not item_id:
            return None, None
        response = self._get(f"/items/{item_id}/prices", {"display_version": "true"})
        if response is None or response.status_code >= 400:
            return None, None
        try:
            prices = response.json().get("prices") or []
        except ValueError:
            return None, None

        promotional_current = None
        references: list[float] = []
        standard = None
        for row in prices:
            amount = _num(row.get("amount"))
            regular = _num(row.get("regular_amount"))
            if row.get("type") == "promotion" and amount:
                promotional_current = amount
                if regular and regular > amount:
                    references.append(regular)
            elif row.get("type") == "standard" and amount:
                standard = amount

        current = promotional_current or standard
        reference = max(references) if references else None
        return current, reference

    def search(self, query: str) -> list[dict[str, Any]]:
        if not self.circuit.can_continue():
            LOG.info("[MELI API] fuente pausada este ciclo: %s", self.circuit.reason)
            return []
        base = {
            "q": query,
            "limit": str(self.limit),
            "offset": "0",
        }
        response = self._get(f"/sites/{SITE_ID}/search", {**base, "sort": "discount_desc"})
        if response is not None and response.status_code == 400:
            # sort=discount_desc no es universal/documentado para todos los
            # sitios. Nunca rompemos el ciclo completo por asumirlo disponible.
            LOG.info("[MELI API] sort=discount_desc rechazado; fallback sin sort.")
            response = self._get(f"/sites/{SITE_ID}/search", base)

        if response is None or response.status_code >= 400:
            status = response.status_code if response is not None else "network"
            if status in (401, 403, 404):
                LOG.warning("[MELI API] acceso no disponible HTTP=%s; se activa fallback web/indexado y no se reintenta en este ciclo.", status)
            else:
                LOG.warning("[MELI API] búsqueda %r HTTP=%s", query, status)
            return []

        try:
            payload = response.json()
        except ValueError:
            LOG.warning("[MELI API] respuesta JSON inválida para %r", query)
            return []

        return payload.get("results") or []

    def discover(self) -> list[dict[str, Any]]:
        raw_queries = os.getenv("ML_HIGH_DEMAND_QUERIES", DEFAULT_QUERIES)
        queries = [q.strip() for q in raw_queries.split(",") if q.strip()]
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        lookups = 0

        for query in queries:
            if not self.circuit.can_continue():
                break
            for item in self.search(query):
                item_id = str(item.get("id") or "")
                if not item_id or item_id in seen:
                    continue
                seen.add(item_id)

                official_id = _official_store_id(item)
                if self.official_only and not official_id:
                    continue

                current = _num(item.get("price"))
                reference = _num(item.get("original_price"))
                api_source = "mercadolibre_public"

                if lookups < self.max_price_lookups:
                    api_current, api_reference = self._price_reference(item_id)
                    lookups += 1
                    if api_current:
                        current = api_current
                    if api_reference:
                        reference = api_reference
                    if api_reference or api_current:
                        api_source = "mercadolibre_prices"
                    time.sleep(self.delay)

                discount = _discount(current, reference)
                if not (MIN_DISCOUNT <= discount <= MAX_DISCOUNT):
                    continue

                url = str(item.get("permalink") or "").strip()
                if not url:
                    continue

                score = min(100, discount + (15 if official_id else 0))
                results.append({
                    "id": item_id,
                    "item_id": item_id,
                    "nombre": item.get("title") or "",
                    "titulo": item.get("title") or "",
                    "marca": _brand(item),
                    "precio_actual": current,
                    "precio_anterior": reference,
                    "descuento": discount,
                    "url": url,
                    "score": score,
                    "puntuacion": score,
                    "es_bomba": discount >= 90,
                    "tienda": "Mercado Libre MX",
                    "categoria": item.get("category_id") or "",
                    "official_store_id": official_id,
                    "origen_link": "mercadolibre_api",
                    "api": api_source,
                })

        LOG.info(
            "[MELI API] %d candidatos válidos; %d consultas de precio; official_only=%s",
            len(results), lookups, self.official_only,
        )
        return results


def buscar_liquidaciones_meli(categoria_o_termino: str = "tecnologia") -> list[dict[str, Any]]:
    """Punto de entrada compatible con el monitor principal."""
    return MercadoLibreApi().discover()


def buscar_mercado_libre_api() -> list[dict[str, Any]]:
    return buscar_liquidaciones_meli()
