"""Parseador determinista de respuestas GraphQL de Walmart/Bodega."""

from __future__ import annotations

from urllib.parse import urljoin

from core.price_sanitizer import sanear_par_precios


class WalmartGraphQLParser:
    def __init__(self, marcas_prioritarias=None, tienda="Walmart MX", dominio="https://www.walmart.com.mx"):
        self.marcas_prioritarias = {
            str(m).strip().lower() for m in (marcas_prioritarias or []) if str(m).strip()
        }
        self.tienda = tienda
        self.dominio = dominio.rstrip("/")

    @staticmethod
    def _nodo_precio(price_info, clave):
        """Lee SOLO el nodo estructurado (p. ej. priceInfo.wasPrice.price).

        Nunca se usan textos como priceString/displayValue ni descripciones: ahí viven
        los gramajes ("875g", "1kg") que antes contaminaban el precio anterior.
        """
        if not isinstance(price_info, dict):
            return None
        nodo = price_info.get(clave)
        if isinstance(nodo, dict):
            return nodo.get("price")
        return nodo   # número crudo; parse_price rechaza cualquier cadena con unidades

    @staticmethod
    def _discount(current, previous):
        if not current or not previous or previous <= current:
            return 0
        return round(((previous - current) / previous) * 100)

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
                nombre = str(prod.get("name") or "").strip()
                brand = str(prod.get("brand") or "Genérica").strip()
                current, previous, motivo = sanear_par_precios(
                    self._nodo_precio(price_info, "currentPrice"),
                    self._nodo_precio(price_info, "wasPrice"),
                    f"{nombre} {prod.get('description') or ''} {prod.get('size') or ''}",
                )
                if current is None:
                    continue

                canonical = str(prod.get("canonicalUrl") or "").strip()
                url = urljoin(self.dominio + "/", canonical)
                descuento = self._discount(current, previous)

                # ONLINE: la liquidación es una rebaja matemática real sobre el precio de lista.
                # Las terminaciones .01/.02/.03 son exclusivas de sucursales FÍSICAS: no suman aquí.
                score = min(descuento, 50)
                if brand.lower() in self.marcas_prioritarias:
                    score += 20
                texto = nombre.lower()
                if any(k in texto for k in ("liquidacion", "liquidación", "outlet", "remate")):
                    score += 10

                if descuento < 50:
                    continue

                productos.append({
                    "id": prod.get("id") or prod.get("productId") or prod.get("upc"),
                    "titulo": nombre,
                    "nombre": nombre,
                    "marca": brand,
                    "precio_actual": current,
                    "precio_anterior": previous,
                    "descuento": descuento,
                    "url": url,
                    "score_api": min(score, 100),
                    "es_bomba": descuento >= 90,
                    "tienda": self.tienda,
                    "origen_link": "graphql",
                    "api": "walmart_graphql",
                })
        except (AttributeError, TypeError, ValueError):
            return []
        return productos
