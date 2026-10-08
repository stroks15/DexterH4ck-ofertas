# scrapers/vtex_stores.py
"""Adaptador para catálogos VTEX con endpoint público/autorizado (p. ej. Chedraui).

El endpoint se configura con CHEDRAUI_VTEX_ENDPOINT (URL de búsqueda de catálogo VTEX,
por ejemplo `.../api/catalog_system/pub/products/search`). Si no está configurado, la
fuente se reporta como `not_configured` y no afecta a las demás tiendas.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urljoin

import requests

from core.scraper_base import BaseScraper, ScraperContext
from core.source_resilience import classify_status

logger = logging.getLogger("DexterH4ck.Vtex")


class VtexStoresScraper(BaseScraper):
    store = "Chedraui"
    endpoint_env = "CHEDRAUI_VTEX_ENDPOINT"
    home = "https://www.chedraui.com.mx"

    def __init__(self, context: ScraperContext | None = None, endpoint: str | None = None) -> None:
        super().__init__(context)
        self.endpoint = (endpoint if endpoint is not None else os.environ.get(self.endpoint_env, "")).strip()
        self.last_status: dict[str, Any] = {"state": "not_run"}

    @staticmethod
    def _offer(product: dict) -> tuple[float | None, float | None]:
        for sku in product.get("items") or []:
            for seller in sku.get("sellers") or []:
                offer = seller.get("commertialOffer") or {}
                if (offer.get("AvailableQuantity") or 0) <= 0:
                    continue
                try:
                    price = float(offer.get("Price"))
                except (TypeError, ValueError):
                    continue
                try:
                    list_price = float(offer.get("ListPrice"))
                except (TypeError, ValueError):
                    list_price = None
                if price > 0:
                    return price, list_price
        return None, None

    def parse(self, payload: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not isinstance(payload, list):
            return rows
        for product in payload:
            if not isinstance(product, dict):
                continue
            price, list_price = self._offer(product)
            link = str(product.get("link") or "").strip()
            title = str(product.get("productName") or "").strip()
            if price is None or not link or not title:
                continue
            reference = list_price if list_price and list_price > price else None
            discount = round((1 - price / reference) * 100) if reference else 0
            rows.append(self.normalize(
                title=title,
                url=urljoin(self.home + "/", link),
                price=price,
                previous_price=reference,
                id=product.get("productId"),
                marca=str(product.get("brand") or ""),
                descuento=discount,
                api="vtex",
            ))
        return rows

    def discover(self) -> list[dict[str, Any]]:
        if not self.endpoint:
            self.last_status = {"state": "not_configured"}
            logger.info("[%s] %s no configurado; fuente VTEX omitida.", self.store, self.endpoint_env)
            return []
        params = {"_from": "0", "_to": "49", "O": "OrderByBestDiscountDESC"}
        try:
            response = self.get(self.endpoint, params=params)
        except requests.Timeout:
            self.last_status = {"state": "timeout"}
            return []
        except requests.RequestException as exc:
            self.last_status = {"state": "network_error", "error": type(exc).__name__}
            return []
        state = classify_status(response.status_code)
        if state != "ok":
            self.last_status = {"state": state, "http": response.status_code}
            logger.warning("[%s] VTEX respondió HTTP %s (%s).", self.store, response.status_code, state)
            return []
        try:
            payload = response.json()
        except ValueError:
            self.last_status = {"state": "invalid_response"}
            return []
        rows = self.parse(payload)
        self.last_status = {"state": "ok" if isinstance(payload, list) else "invalid_response", "count": len(rows)}
        return rows
