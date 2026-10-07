"""Adaptadores VTEX/JSON configurables para Coppel y Suburbia.

No se asume que los dominios públicos sean endpoints VTEX. Para activar una
API JSON/VTEX, configure COPPEL_API_ENDPOINT o SUBURBIA_API_ENDPOINT con el
endpoint autorizado por el comercio. El scraper conserva el flujo HTML público
existente como fallback.
"""

from __future__ import annotations

import logging
from typing import Any

from curl_cffi import requests as curl_requests

from config.endpoints import ENDPOINTS_REALES

logger = logging.getLogger("DexterH4ck.VtexScraper")


class VtexStoresScraper:
    def __init__(self, context=None):
        self.context = context
        self.session = curl_requests.Session()
        self.coppel_api = (
            getattr(context, "COPPEL_API_ENDPOINT", None)
            or ENDPOINTS_REALES.get("COPPEL_API")
        )
        self.suburbia_api = (
            getattr(context, "SUBURBIA_API_ENDPOINT", None)
            or ENDPOINTS_REALES.get("SUBURBIA_API")
        )
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cache-Control": "no-cache",
            }
        )

    @staticmethod
    def _is_configured_api(url: str | None, env_name: str) -> bool:
        """Solo usa la URL si fue configurada explícitamente como API."""
        import os

        configured = os.getenv(env_name, "").strip()
        return bool(configured and url and url == configured)

    def _execute_vtex_request(
        self, url: str | None, query: str, store_name: str, env_name: str
    ) -> list[dict[str, Any]]:
        if not self._is_configured_api(url, env_name):
            logger.info("[%s] API VTEX no configurada; se conserva fallback público.", store_name.upper())
            return []

        params = {
            "ft": query,
            "_from": "0",
            "_to": "49",
            "O": "OrderByBestDiscountDESC",
        }
        try:
            response = self.session.get(url, params=params, timeout=25.0)
            if response.status_code != 200:
                logger.warning("[%s] VTEX HTTP %s", store_name.upper(), response.status_code)
                return []
            payload = response.json()
            return payload if isinstance(payload, list) else []
        except ValueError as exc:
            logger.warning("[%s] Error VTEX controlado: %s", store_name.upper(), exc)
        except Exception as exc:
            logger.warning("[%s] Error VTEX inesperado: %s", store_name.upper(), exc)
        return []

    @staticmethod
    def _normalize_vtex_item(item: dict[str, Any], tienda: str) -> dict[str, Any]:
        try:
            skus = item.get("items") or []
            if not skus:
                return {}
            sellers = skus[0].get("sellers") or []
            if not sellers:
                return {}
            offer = sellers[0].get("commertialOffer") or {}
            precio_actual = float(offer.get("Price") or 0)
            precio_anterior = float(offer.get("ListPrice") or 0)
            if precio_anterior <= precio_actual:
                precio_anterior = None
            return {
                "id": item.get("productId", ""),
                "name": item.get("productName", ""),
                "titulo": item.get("productName", ""),
                "brand": item.get("brand", "Genérico"),
                "marca": item.get("brand", "Genérico"),
                "canonical_url": item.get("link", ""),
                "url": item.get("link", ""),
                "precio_actual": precio_actual,
                "precio_anterior": precio_anterior,
                "tienda": tienda,
                "disponibilidad": bool(offer.get("AvailableQuantity", 0)),
                "origen_link": "api_vtex",
            }
        except (TypeError, ValueError, AttributeError):
            return {}

    def fetch_coppel_liquidations(self, query: str = "ofertas") -> list[dict[str, Any]]:
        raw_items = self._execute_vtex_request(
            self.coppel_api, query, "Coppel", "COPPEL_API_ENDPOINT"
        )
        return [
            normalized
            for item in raw_items
            if (normalized := self._normalize_vtex_item(item, "Coppel"))
        ]

    def fetch_suburbia_liquidations(self, query: str = "promociones") -> list[dict[str, Any]]:
        raw_items = self._execute_vtex_request(
            self.suburbia_api, query, "Suburbia", "SUBURBIA_API_ENDPOINT"
        )
        return [
            normalized
            for item in raw_items
            if (normalized := self._normalize_vtex_item(item, "Suburbia"))
        ]
