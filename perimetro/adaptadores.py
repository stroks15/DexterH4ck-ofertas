# perimetro/adaptadores.py
from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterable

from . import extraccion
from .registry import Tienda, seleccionar, tiendas_activas
from .transporte import Respuesta, Transporte

try:
    from core.scraper_base import BaseScraper, ScraperContext, run_scrapers_parallel  # type: ignore
except ImportError:  # pragma: no cover
    class ScraperContext:  # type: ignore
        def __init__(self, *a, **k) -> None:
            pass

    class BaseScraper:  # type: ignore
        def __init__(self, contexto: Any = None) -> None:
            self.context = contexto or ScraperContext()

        def normalize(self, **datos: Any) -> dict:
            return dict(datos)

    def run_scrapers_parallel(scrapers: Iterable[Any], max_workers: int | None = None) -> list[dict]:  # type: ignore
        salida: list[dict] = []
        for scraper in scrapers:
            salida.extend(scraper.discover() or [])
        return salida

try:
    from core.offer_identity import canonical_store  # type: ignore
except ImportError:  # pragma: no cover
    def canonical_store(valor: Any) -> str:  # type: ignore
        return re.sub(r"[^a-z0-9]+", " ", str(valor or "").lower()).strip()

logger = logging.getLogger("DexterH4ck.Perimetro")

TERMINOS = ("liquidacion", "remate", "outlet", "descuento", "ultimas piezas", "mega rebajas")


def _terminos(ident: str) -> tuple[str, ...]:
    crudo = os.environ.get(f"PERIMETRO_{ident.upper()}_QUERIES", "") or os.environ.get("PERIMETRO_QUERIES", "")
    pedidos = tuple(t.strip() for t in crudo.split(",") if t.strip())
    return pedidos or TERMINOS[:3]


def _entero(nombre: str, defecto: int, minimo: int = 1, maximo: int = 10) -> int:
    try:
        return max(minimo, min(maximo, int(os.environ.get(nombre, "") or defecto)))
    except (TypeError, ValueError):
        return defecto


class AdaptadorTienda(BaseScraper):
    def __init__(self, tienda: Tienda, contexto: ScraperContext | None = None, transporte: Transporte | None = None) -> None:
        super().__init__(contexto)
        self.tienda = tienda
        self.store = tienda.nombre
        self.transporte = transporte or Transporte()
        self.last_status: dict[str, Any] = {"state": "not_run"}
        self.estrategia_ganadora = ""

    def discover(self) -> list[dict[str, Any]]:
        if not self.tienda.busqueda and not self.tienda.vtex_host and not self.tienda.sfcc_site:
            self.last_status = {"state": "not_configured", "detail": "tienda sin búsqueda declarada"}
            return []

        maximo = _entero("PERIMETRO_MAX_POR_TIENDA", 40, 1, 200)
        paginas = _entero("PERIMETRO_MAX_PAGINAS", 1, 1, 5)
        filas: list[dict] = []
        errores: list[str] = []
        for termino in _terminos(self.tienda.id):
            for estrategia in self.tienda.estrategias:
                obtenidas = self._estrategia(estrategia, termino, paginas)
                if obtenidas:
                    self.estrategia_ganadora = self.estrategia_ganadora or estrategia
                    filas.extend(obtenidas)
                elif self.last_status.get("state") in ("cooldown", "blocked_waf", "captcha_required"):
                    errores.append(str(self.last_status.get("state")))
            if len(filas) >= maximo:
                break
            if errores and errores[-1] in ("cooldown", "blocked_waf", "captcha_required"):
                break

        filas = extraccion.deduplicar(filas)[:maximo]
        if filas:
            self.last_status = {"state": "ok", "count": len(filas), "estrategia": self.estrategia_ganadora}
        elif errores:
            self.last_status = {"state": errores[-1], "count": 0}
        else:
            self.last_status = {"state": "empty", "count": 0, "estrategia": self.tienda.estrategias[0]}
        logger.info("[perimetro:%s] %s (%s filas)", self.tienda.id, self.last_status.get("state"), len(filas))
        return filas

    def _estrategia(self, estrategia: str, termino: str, paginas: int) -> list[dict]:
        try:
            if estrategia == "vtex":
                return self._vtex(termino, paginas)
            if estrategia == "sfcc":
                return self._sfcc(termino)
            return self._html(estrategia, termino)
        except Exception as exc:
            self.last_status = {"state": "invalid_response", "detail": type(exc).__name__}
            return []

    def _url(self, termino: str) -> str:
        return self.tienda.url_busqueda(termino) or self.tienda.home

    def _vtex(self, termino: str, paginas: int) -> list[dict]:
        host = self.tienda.vtex_host or self.tienda.home
        endpoint = host.rstrip("/") + "/api/catalog_system/pub/products/search"
        salida: list[dict] = []
        for pagina in range(paginas):
            respuesta = self.transporte.get(endpoint, motor="http", referer=self.tienda.home, extra={"_from": str(pagina * 50), "_to": str(pagina * 50 + 49), "ft": termino})
            self._anotar(respuesta)
            if not respuesta.ok:
                break
            try:
                payload = __import__("json").loads(respuesta.texto or "[]")
            except ValueError:
                break
            if not isinstance(payload, list) or not payload:
                break
            salida.extend(extraccion.vtex(payload, self.tienda.nombre, self.tienda.home))
            if len(payload) < 50:
                break
        return salida

    def _sfcc(self, termino: str) -> list[dict]:
        if not self.tienda.sfcc_site:
            return []
        base = self.tienda.home.rstrip("/")
        sugerencias = f"{base}/on/demandware.store/{self.tienda.sfcc_site}/es_MX/SearchServices-GetSuggestions?q={termino}&limit=20"
        respuesta = self.transporte.get(sugerencias, motor="http", referer=self.tienda.home)
        self._anotar(respuesta)
        if respuesta.ok:
            try:
                filas = extraccion.sfcc(__import__("json").loads(respuesta.texto or "{}"), self.tienda.nombre, self.tienda.home)
            except ValueError:
                filas = []
            if filas:
                return filas
        pagina = self.transporte.get(self._url(termino), motor="http" if not self.tienda.requiere_js else "stealth")
        self._anotar(pagina)
        if not pagina.ok:
            return []
        return extraccion.selectores(pagina.texto, self.tienda.nombre, self.tienda.home, self.tienda.selectores)

    def _html(self, estrategia: str, termino: str) -> list[dict]:
        motor = "stealth" if self.tienda.requiere_js else "http"
        respuesta = self.transporte.get(self._url(termino), motor=motor, referer=self.tienda.home, espera_selector=self.tienda.selectores.get("card", ""))
        self._anotar(respuesta)
        if not respuesta.ok:
            return []
        if estrategia == "nextdata":
            return extraccion.nextdata(respuesta.texto, self.tienda.nombre, self.tienda.home)
        if estrategia == "jsonld":
            return extraccion.jsonld(respuesta.texto, self.tienda.nombre, self.tienda.home)
        if estrategia == "amazon":
            return extraccion.amazon(respuesta.texto, self.tienda.nombre, self.tienda.home, self.tienda.selectores)
        return extraccion.selectores(respuesta.texto, self.tienda.nombre, self.tienda.home, self.tienda.selectores)

    def _anotar(self, respuesta: Respuesta) -> None:
        if respuesta.ok:
            if self.last_status.get("state") in (None, "not_run", "empty"):
                self.last_status = {"state": "ok", "count": 0}
            return
        self.last_status = {"state": respuesta.estado, "http": respuesta.http, "waf": respuesta.waf, "motor": respuesta.motor}


def adaptadores(tiendas: list[Tienda] | None = None, contexto: ScraperContext | None = None) -> list[AdaptadorTienda]:
    transporte = Transporte()
    return [AdaptadorTienda(t, contexto, transporte) for t in (tiendas or tiendas_activas())]


class PerimetroScraper:
    def __init__(self, contexto: ScraperContext | None = None, nombres: list[str] | None = None) -> None:
        self.contexto = contexto
        self.scrapers = adaptadores(seleccionar(nombres), contexto)

    def discover(self) -> list[dict[str, Any]]:
        return run_scrapers_parallel(self.scrapers, max_workers=_entero("PERIMETRO_CONCURRENCIA", 4, 1, 8))

    def estado(self) -> dict[str, Any]:
        return {s.tienda.id: s.last_status for s in self.scrapers}
