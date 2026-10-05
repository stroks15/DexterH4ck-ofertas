"""Parseador determinista de respuestas GraphQL de Walmart/Bodega."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin


class WalmartGraphQLParser:
    def __init__(self, marcas_prioritarias=None, tienda="Walmart MX", dominio="https://www.walmart.com.mx"):
        self.marcas_prioritarias = {
            str(m).strip().lower() for m in (marcas_prioritarias or []) if str(m).strip()
        }
        self.tienda = tienda
        self.dominio = dominio.rstrip("/")

    @staticmethod
    def _money(value):
        try:
            if value in (None, ""):
                return None
            return Decimal(str(value).replace(",", "").replace("$", "").strip())
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _discount(current, previous):
        if not current or not previous or previous <= current:
            return 0
        return round(((previous - current) / previous) * 100)

    @staticmethod
    def _physical_centavos(current):
        if current is None:
            return False
        centavos = (current * 100) % 100
        return centavos in (Decimal("1"), Decimal("2"), Decimal("3"))

    def parsear_respuesta_busqueda(self, json_response: dict) -> list[dict]:
        productos = []
        try:
            data = json_response.get("data", {}) if isinstance(json_response, dict) else {}
            search = data.get("search") or data.get("searchAndFilter") or data.get("SearchAndFilter") or {}
            rows = search.get("products") if isinstance(search, dict) else []
            if not isinstance(rows, list):
                rows = data.get("products") or []
            if not isinstance(rows, list):
                return []

            for prod in rows:
                if not isinstance(prod, dict):
                    continue
                price_info = prod.get("priceInfo") or prod.get("price") or {}
                current_node = price_info.get("currentPrice") if isinstance(price_info, dict) else None
                current = self._money(
                    (current_node or {}).get("price") if isinstance(current_node, dict) else current_node
                )
                previous_node = price_info.get("wasPrice") if isinstance(price_info, dict) else None
                previous = self._money(
                    (previous_node or {}).get("price") if isinstance(previous_node, dict) else previous_node
                )
                if current is None or current <= 0:
                    continue

                nombre = str(prod.get("name") or "").strip()
                brand = str(prod.get("brand") or "Genérica").strip()
                canonical = str(prod.get("canonicalUrl") or "").strip()
                url = urljoin(self.dominio + "/", canonical)
                descuento = self._discount(current, previous)
                es_bomba = self._physical_centavos(current)

                score = min(descuento, 50)
                if es_bomba:
                    score += 30
                if brand.lower() in self.marcas_prioritarias:
                    score += 20
                texto = nombre.lower()
                if any(k in texto for k in ("liquidacion", "liquidación", "outlet", "remate")):
                    score += 10

                if descuento < 50 and not es_bomba:
                    continue

                productos.append({
                    "id": prod.get("id") or prod.get("productId") or prod.get("upc"),
                    "titulo": nombre,
                    "nombre": nombre,
                    "marca": brand,
                    "precio_actual": float(current),
                    "precio_anterior": float(previous) if previous and previous > current else None,
                    "descuento": descuento,
                    "url": url,
                    "score_api": min(score, 100),
                    "es_bomba": es_bomba or descuento >= 90,
                    "tienda": self.tienda,
                    "origen_link": "graphql",
                    "api": "walmart_graphql",
                })
        except (AttributeError, TypeError, ValueError):
            return []
        return productos
