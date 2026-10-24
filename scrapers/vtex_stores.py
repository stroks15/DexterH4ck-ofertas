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
from scrapers.discovery_utils import ESTADOS_CORTE, TERMINOS_LIQUIDACION, env_int, env_terminos

logger = logging.getLogger("DexterH4ck.Vtex")


class VtexStoresScraper(BaseScraper):
    store = "Chedraui"
    endpoint_env = "CHEDRAUI_VTEX_ENDPOINT"
    home = "https://www.chedraui.com.mx"
    PAGE_SIZE = 50  # máximo de VTEX por petición

    def __init__(self, context: ScraperContext | None = None, endpoint: str | None = None) -> None:
        super().__init__(context)
        self.endpoint = (endpoint if endpoint is not None else os.environ.get(self.endpoint_env, "")).strip()
        self.last_status: dict[str, Any] = {"state": "not_run"}

    @staticmethod
    def _offer(product: dict) -> tuple[float | None, float | None]:
        """Mejor oferta disponible del producto: mayor descuento; a igual descuento, menor precio."""
        mejor = None
        for sku in product.get("items") or []:
            for seller in sku.get("sellers") or []:
                offer = seller.get("commertialOffer") or {}
                if (offer.get("AvailableQuantity") or 0) <= 0:
                    continue
                try:
                    price = float(offer.get("Price"))
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                list_price = None
                for campo in ("ListPrice", "PriceWithoutDiscount"):
                    try:
                        candidato = float(offer.get(campo))
                    except (TypeError, ValueError):
                        continue
                    if candidato > price:
                        list_price = candidato
                        break
                descuento = (1 - price / list_price) if list_price else 0.0
                clave = (descuento, -price)
                if mejor is None or clave > mejor[0]:
                    mejor = (clave, price, list_price)
        return (mejor[1], mejor[2]) if mejor else (None, None)

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
            fila = self.normalize(
                    title=title,
                    url=urljoin(self.home + "/", link),
                    price=price,
                    previous_price=reference,
                    id=product.get("productId"),
                    marca=str(product.get("brand") or ""),
                    descuento=discount,
                    api="vtex",
                )
            fila["tipo_fuente"] = "ONLINE"
            rows.append(fila)
        return rows

    def _pedir_pagina(self, params: dict) -> tuple[str, Any]:
        """Devuelve (estado, payload). Nunca lanza."""
        try:
            response = self.get(self.endpoint, params=params)
        except requests.Timeout:
            return "timeout", None
        except requests.RequestException:
            return "network_error", None
        state = classify_status(response.status_code)
        if state != "ok":
            logger.warning("[%s] VTEX respondió HTTP %s (%s).", self.store, response.status_code, state)
            self.last_status = {"state": state, "http": response.status_code}
            return state, None
        try:
            return "ok", response.json()
        except ValueError:
            return "invalid_response", None

    def discover(self) -> list[dict[str, Any]]:
        if not self.endpoint:
            self.last_status = {"state": "not_configured"}
            logger.info("[%s] %s no configurado; fuente VTEX omitida.", self.store, self.endpoint_env)
            return []

        paginas = env_int("CHEDRAUI_VTEX_PAGES", 3, 1, 10)
        # "" = catálogo completo ordenado por mayor descuento; el resto, texto libre (ft).
        terminos = env_terminos("CHEDRAUI_QUERIES", ("",) + TERMINOS_LIQUIDACION[:3])
        vistos: set = set()
        rows: list[dict[str, Any]] = []
        ultimo_error = None

        for termino in terminos:
            for pagina in range(paginas):
                desde = pagina * self.PAGE_SIZE
                params = {"_from": str(desde), "_to": str(desde + self.PAGE_SIZE - 1), "O": "OrderByBestDiscountDESC"}
                if termino:
                    params["ft"] = termino
                estado, payload = self._pedir_pagina(params)
                if estado != "ok":
                    ultimo_error = estado
                    break  # un bloqueo/credencial/endpoint inválido corta toda la fuente (abajo)
                if not isinstance(payload, list):
                    ultimo_error = "invalid_response"
                    break
                for fila in self.parse(payload):
                    clave = fila.get("id") or fila.get("url")
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    rows.append(fila)
                if len(payload) < self.PAGE_SIZE:
                    break
            if ultimo_error in ESTADOS_CORTE:
                break

        if rows:
            self.last_status = {"state": "ok", "count": len(rows)}
            if ultimo_error:
                self.last_status["partial"] = ultimo_error
        elif ultimo_error:
            self.last_status = (
                dict(self.last_status)
                if self.last_status.get("state") not in ("not_run", "ok")
                else {"state": ultimo_error}
            )
        else:
            self.last_status = {"state": "ok", "count": 0}
        return rows
