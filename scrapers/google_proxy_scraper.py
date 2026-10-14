# scrapers/google_proxy_scraper.py
"""Scraper que consume la Web App propia en Google Apps Script (proxy unificado).

La URL del despliegue se lee de la variable de entorno ``APPS_SCRIPT_PROXY_URL``.
NUNCA se imprime ni se registra: suele incluir el ID del despliegue/clave de acceso.

Notas técnicas:
- Apps Script contesta con un HTTP 302 hacia ``script.googleusercontent.com``; por eso
  la petición GET se hace con ``allow_redirects=True`` (obligatorio).
- El JSON unificado puede traer Walmart, Bodega Aurrera, Mercado Libre, Chedraui y
  Soriana. Este adaptador es tolerante con el formato (lista plana, o dict con la lista
  bajo ``ofertas/productos/items/data/results``, o dict por tienda).
- Los precios pasan por ``core.price_sanitizer``: solo se aceptan nodos numéricos de
  precio; un gramaje ("875g", "1kg", "400ml") jamás se interpreta como precio anterior.
- Es una fuente ONLINE: no se heredan ``tipo_fuente`` ni puntajes/descuentos precalculados
  del proxy. El descuento lo recalcula el monitor desde precios saneados.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from core.price_sanitizer import sanear_par_precios
from core.scraper_base import BaseScraper, ScraperContext
from core.source_resilience import classify_status

logger = logging.getLogger("DexterH4ck.GoogleProxy")

ENV_URL = "APPS_SCRIPT_PROXY_URL"

LISTA_KEYS = ("ofertas", "offers", "productos", "products", "items", "data", "results", "resultados", "deals")

# Alias de tienda (en minúsculas, sin acentos/espacios) -> nombre canónico del monitor.
TIENDAS = (
    (("walmart",), "Walmart MX"),
    (("bodegaaurrera", "bodega"), "Bodega Aurrera"),
    (("mercadolibre", "meli", "mlm"), "Mercado Libre MX"),
    (("chedraui",), "Chedraui"),
    (("soriana",), "Soriana"),
)

HOSTS = {
    "Walmart MX": "https://www.walmart.com.mx",
    "Bodega Aurrera": "https://www.bodegaaurrera.com.mx",
    "Mercado Libre MX": "https://www.mercadolibre.com.mx",
    "Chedraui": "https://www.chedraui.com.mx",
    "Soriana": "https://www.soriana.com",
}

K_TITULO = ("titulo", "title", "nombre", "name", "productName", "displayName")
K_URL = ("url", "link", "permalink", "canonicalUrl", "productUrl")
K_ID = ("id", "productId", "sku", "itemId", "upc", "gtin")
K_ACTUAL = ("precio_actual", "currentPrice", "price", "precio", "salePrice", "sellingPrice", "offerPrice")
K_ANTERIOR = (
    "precio_anterior",
    "wasPrice",
    "previous_price",
    "originalPrice",
    "listPrice",
    "regularPrice",
    "precio_lista",
    "precio_regular",
    "original_price",
)
K_TIENDA = ("tienda", "store", "source", "fuente", "retailer", "marketplace")


def _canonica(valor: Any) -> str | None:
    texto = "".join(ch for ch in str(valor or "").lower() if ch.isalnum())
    for alias, nombre in TIENDAS:
        if any(a in texto for a in alias):
            return nombre
    return None


def _tienda_por_url(url: str) -> str | None:
    """Infiere la tienda por dominio cuando el proxy entrega una fila sin campo tienda."""
    try:
        host = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    dominios = {
        "Walmart MX": ("walmart.com.mx",),
        "Bodega Aurrera": ("bodegaaurrera.com.mx",),
        "Mercado Libre MX": ("mercadolibre.com.mx", "mercadolibre.com", "mercadolibre.com.ar"),
        "Chedraui": ("chedraui.com.mx",),
        "Soriana": ("soriana.com",),
    }
    for tienda, bases in dominios.items():
        if any(host == base or host.endswith("." + base) for base in bases):
            return tienda
    return None


def _primero(nodo: dict, claves: tuple[str, ...]) -> Any:
    for clave in claves:
        valor = nodo.get(clave)
        if valor not in (None, ""):
            return valor
    return None


def _nodo_precio(nodo: dict, claves: tuple[str, ...]) -> Any:
    """Valor de precio estructurado: directo o dentro de priceInfo/prices (p. ej.
    ``priceInfo.wasPrice.price``). Devuelve el crudo; la validación la hace el sanitizador."""
    valor = _primero(nodo, claves)
    if valor is not None:
        return valor
    for contenedor in ("priceInfo", "prices", "pricing"):
        sub = nodo.get(contenedor)
        if isinstance(sub, dict):
            valor = _primero(sub, claves)
            if valor is not None:
                return valor
    return None


class GoogleProxyScraper(BaseScraper):
    """Consume el JSON unificado de la Web App de Google Apps Script."""

    store = "Google Proxy"

    def __init__(self, context: ScraperContext | None = None, url: str | None = None) -> None:
        super().__init__(context)
        self.url = (url if url is not None else os.environ.get(ENV_URL, "")).strip()
        self.last_status: dict[str, Any] = {"state": "not_run"}

    # ------------------------------------------------------------------ red
    def _descargar(self) -> Any:
        """GET con redirecciones (Apps Script responde 302). Devuelve JSON o None."""
        try:
            response = self.get(self.url, allow_redirects=True)
        except requests.Timeout:
            self.last_status = {"state": "timeout"}
            return None
        except requests.RequestException as exc:
            self.last_status = {"state": "network_error", "detail": type(exc).__name__}
            return None
        estado = classify_status(response.status_code)
        if estado != "ok":
            self.last_status = {"state": estado, "http": response.status_code}
            logger.warning("[Google Proxy] HTTP %s (%s).", response.status_code, estado)
            return None
        try:
            return response.json()
        except ValueError:
            # Típico cuando el despliegue no es público y Google devuelve una página de login HTML.
            self.last_status = {
                "state": "invalid_response",
                "detail": "la respuesta no es JSON (¿acceso del despliegue = 'Cualquier persona'?)",
            }
            return None

    # --------------------------------------------------------------- parseo
    @staticmethod
    def _filas(payload: Any) -> list[tuple[dict, str | None]]:
        """Aplana respuestas de Apps Script y catálogos anidados sin perder la tienda.

        Acepta listas planas, {ofertas:[...]}, {walmart:[...]}, y estructuras como
        {data:{search:{products:[...]}}} o {data:{walmart:[...]}}.
        """
        filas: list[tuple[dict, str | None]] = []
        vistos: set[int] = set()

        def recorrer(nodo: Any, tienda: str | None = None) -> None:
            if isinstance(nodo, list):
                for fila in nodo:
                    if isinstance(fila, dict):
                        # Un objeto producto suele tener al menos un campo de título/precio/URL.
                        tiene_campos_producto = any(
                            clave in fila for clave in (*K_TITULO, *K_URL, *K_ACTUAL, "priceInfo", "prices")
                        )
                        if tiene_campos_producto:
                            filas.append((fila, tienda))
                        else:
                            recorrer(fila, tienda)
                return
            if not isinstance(nodo, dict) or id(nodo) in vistos:
                return
            vistos.add(id(nodo))

            # Algunas respuestas encapsulan la lista en una clave conocida.
            for clave in LISTA_KEYS:
                if clave in nodo and isinstance(nodo[clave], (list, dict)):
                    recorrer(nodo[clave], _canonica(clave) or tienda)

            # Las claves por tienda heredan el nombre de la tienda a sus productos.
            for clave, valor in nodo.items():
                tienda_hija = _canonica(clave) or tienda
                if isinstance(valor, (dict, list)):
                    if clave not in LISTA_KEYS:
                        recorrer(valor, tienda_hija)

            # Payload de producto directo, no encapsulado en una lista.
            if any(clave in nodo for clave in (*K_TITULO, *K_URL, *K_ACTUAL, "priceInfo", "prices")):
                filas.append((nodo, tienda))

        recorrer(payload)
        # Evitar que una misma lista se visite por la clave conocida y por el recorrido genérico.
        unicas: list[tuple[dict, str | None]] = []
        ids: set[int] = set()
        for fila, tienda in filas:
            if id(fila) not in ids:
                ids.add(id(fila))
                unicas.append((fila, tienda))
        return unicas

    def _normalizar(self, fila: dict, tienda_heredada: str | None) -> dict[str, Any] | None:
        titulo = str(_primero(fila, K_TITULO) or "").strip()
        url = str(_primero(fila, K_URL) or "").strip()
        tienda = _canonica(_primero(fila, K_TIENDA)) or tienda_heredada or _tienda_por_url(url)
        if not tienda or not titulo or not url:
            return None
        url = urljoin(HOSTS.get(tienda, "") + "/", url)
        if not url.startswith(("http://", "https://")):
            return None

        actual, anterior, motivo = sanear_par_precios(
            _nodo_precio(fila, K_ACTUAL),
            _nodo_precio(fila, K_ANTERIOR),
            f"{titulo} {fila.get('descripcion') or fila.get('description') or ''} "
            f"{fila.get('gramaje') or ''} {fila.get('size') or ''}",
        )
        if actual is None:
            return None
        if motivo in ("anterior_no_numerico", "anterior_igual_a_gramaje"):
            logger.warning("[Google Proxy] referencia descartada (%s): %s", motivo, titulo[:60])

        producto_id = _primero(fila, K_ID)
        return self.normalize(
            title=titulo,
            url=url,
            price=actual,
            previous_price=anterior,
            id=str(producto_id) if producto_id is not None else url,
            marca=str(fila.get("marca") or fila.get("brand") or ""),
            categoria=str(fila.get("categoria") or fila.get("category") or ""),
            imagen=fila.get("imagen") or fila.get("image") or None,
        ) | {
            "tienda": tienda,
            "origen_link": "google_proxy",
            "api": "google_apps_script",
            "tipo_fuente": "ONLINE",  # nunca FISICA: las terminaciones .01/.02/.03 no aplican
        }

    # ----------------------------------------------------------------- API
    def discover(self) -> list[dict[str, Any]]:
        if not self.url:
            self.last_status = {"state": "not_configured", "detail": f"{ENV_URL} no configurado"}
            logger.info("[Google Proxy] %s no configurado; fuente omitida.", ENV_URL)
            return []
        payload = self._descargar()
        if payload is None:
            return []
        proxy_status = str(payload.get("status") or "") if isinstance(payload, dict) else ""
        proxy_sources = payload.get("fuentes") if isinstance(payload, dict) else None
        filas = self._filas(payload)
        rows: list[dict[str, Any]] = []
        descartadas = 0
        vistos: set = set()
        for fila, tienda in filas:
            producto = self._normalizar(fila, tienda)
            if not producto:
                descartadas += 1
                continue
            clave = (producto["tienda"], producto["id"])
            if clave in vistos:
                continue
            vistos.add(clave)
            rows.append(producto)
        if rows:
            self.last_status = {
                "state": "ok",
                "count": len(rows),
                "rows_seen": len(filas),
                "rejected_rows": descartadas,
            }
            if proxy_status:
                self.last_status["proxy_status"] = proxy_status
            if isinstance(proxy_sources, list):
                self.last_status["fuentes"] = proxy_sources
        else:
            if proxy_status == "error":
                estado = "error"
            elif filas:
                estado = "empty"
            elif isinstance(payload, dict) and any(k in payload for k in ("offers", "ofertas", "productos", "results")):
                estado = "empty"
            else:
                estado = "invalid_response"
            self.last_status = {
                "state": estado,
                "count": 0,
                "rows_seen": len(filas),
                "rejected_rows": descartadas,
                "detail": (
                    "el proxy reportó un fallo global"
                    if proxy_status == "error"
                    else (
                        "el JSON no contiene filas de producto reconocibles"
                        if not filas
                        else "se encontraron filas, pero ninguna pasó la normalización"
                    )
                ),
            }
            if proxy_status:
                self.last_status["proxy_status"] = proxy_status
            if isinstance(proxy_sources, list):
                self.last_status["fuentes"] = proxy_sources
            logger.warning(
                "[Google Proxy] %s: filas=%s, descartadas=%s.", self.last_status["detail"], len(filas), descartadas
            )
        return rows


def buscar_google_proxy() -> list[dict[str, Any]]:
    """Función de módulo (mismo patrón que los demás scrapers)."""
    return GoogleProxyScraper().discover()
