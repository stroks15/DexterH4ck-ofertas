# scrapers/scrapling_adaptador.py
# Adaptadores por tienda sobre el motor. Si el motor falla, cae al scraper legacy
# equivalente y lo marca en last_status (nunca se queda mudo).
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from core.scraper_base import BaseScraper, ScraperContext, run_scrapers_parallel
from core.scrapling_motor import Config, Motor, Resultado, disponible, motor as motor_global

logger = logging.getLogger("DexterH4ck.AdaptadorScrapling")

TERMINOS = ("liquidacion", "remate", "outlet")


def _terminos(tienda: str) -> tuple[str, ...]:
    crudo = os.environ.get(f"{tienda.upper()}_QUERIES", "") or os.environ.get("SCRAPLING_QUERIES", "")
    pedidos = tuple(t.strip() for t in crudo.split(",") if t.strip())
    return pedidos or TERMINOS


def _entero(nombre: str, defecto: int, minimo: int = 1, maximo: int = 20) -> int:
    try:
        return max(minimo, min(maximo, int(os.environ.get(nombre, "") or defecto)))
    except (TypeError, ValueError):
        return defecto


class AdaptadorScrapling(BaseScraper):
    tienda = "unknown"

    def __init__(self, contexto: ScraperContext | None = None, motor: Motor | None = None, respaldo: Callable[[], list[dict]] | None = None, selectores: dict[str, str] | None = None) -> None:
        super().__init__(contexto)
        self.store = self.tienda
        self._motor = motor
        self._respaldo = respaldo
        self.selectores = selectores or {}
        self.last_status: dict[str, Any] = {"state": "not_run"}

    @property
    def motor(self) -> Motor:
        if self._motor is None:
            self._motor = motor_global()
        return self._motor

    def _cfg(self) -> dict:
        return (self.motor.config.tiendas or {}).get(self.tienda.lower(), {})

    def _url(self, termino: str) -> str:
        return (self._cfg().get("url") or "").replace("{q}", termino.replace(" ", "+"))

    def discover(self) -> list[dict[str, Any]]:
        if not self._cfg().get("url"):
            self.last_status = {"state": "not_configured", "detail": "sin url en config/scrapling_motor.json"}
            return self._legacy("not_configured")

        limite = _entero("SCRAPLING_MAX_POR_TIENDA", 30)
        filas: list[dict] = []
        for termino in _terminos(self.tienda):
            resultado = self.motor.pedir_o_fallback_sync(
                self._url(termino),
                self._respaldo,
                espera_selector=self.selectores.get("card", ""),
                filas_de=lambda r: self._extraer(r),
            )
            if resultado.filas:
                filas.extend(resultado.filas)
            self._anotar(resultado)
            if resultado.filas or resultado.bloqueado or len(filas) >= limite:
                break

        filas = self._normalizar(filas)[:limite]
        if filas:
            self.last_status.update({"state": "ok", "count": len(filas)})
        return filas

    def _extraer(self, resultado: Resultado) -> list[dict]:
        if not resultado.texto or not self.selectores:
            return []
        return self._normalizar(self.motor.extraer(resultado, self.selectores))

    def _normalizar(self, filas: list[dict]) -> list[dict]:
        salida = []
        for fila in filas:
            if not fila.get("titulo") or not fila.get("url"):
                continue
            if not str(fila["url"]).startswith("http"):
                base = (self._cfg().get("url") or "").split("/search")[0].split("/buscar")[0].rstrip("/")
                fila["url"] = base + "/" + str(fila["url"]).lstrip("/")
            fila.setdefault("tienda", self.tienda)
            fila.setdefault("origen_link", "motor_scrapling")
            fila.setdefault("tipo_fuente", "ONLINE")
            salida.append(fila)
        return salida

    def _anotar(self, resultado: Resultado) -> None:
        self.last_status = {"state": "ok" if resultado.ok else resultado.estado, "motor": resultado.motor or "scrapling", "perfil": resultado.perfil, "fallback": resultado.fallback, "http": resultado.http}

    def _legacy(self, motivo: str) -> list[dict]:
        if self._respaldo is None:
            return []
        try:
            filas = self._respaldo() or []
        except Exception as exc:
            self.last_status = {"state": "error", "detail": type(exc).__name__}
            return []
        self.last_status = {"state": "fallback", "detail": motivo, "fallback": True, "count": len(filas)}
        return filas


def _respaldo_de(nombre: str) -> Callable[[], list[dict]] | None:
    try:
        from scrapers import api_stores

        for scraper in api_stores.api_first_scrapers(None):
            if scraper.store.lower() == nombre.lower() and not getattr(scraper, "tienda", None):
                return scraper.discover
    except Exception:
        return None
    return None


def _selectores(ident: str) -> dict[str, str]:
    comunes = {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}
    especiales = {
        "walmart": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]"},
        "bodega": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]"},
        "sams": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]"},
        "amazon": {"card": "div[data-component-type=\"s-search-result\"]", "title": "h2 span", "price": "span.a-price-whole", "link": "h2 a"},
        "nike": {"card": "div.product-card", "title": "div.product-card__title", "price": "div.product-price", "link": "a.product-card__link-overlay"},
        "adidas": {"card": "div[data-auto-id=\"product-card\"]", "title": "div[data-auto-id=\"product-card-description\"] span", "price": "div[data-auto-id=\"product-price\"]", "link": "a[href]"},
        "palacio": {"card": "div.product-tile", "title": "a.link", "price": "span.product-sales-price", "link": "a.link"},
        "officedepot": {"card": "div.product-tile", "title": "a.link", "price": "span.product-sales-price", "link": "a.link"},
    }
    return {**comunes, **especiales.get(ident, {})}


def adaptadores_motor(contexto: ScraperContext | None = None) -> list[AdaptadorScrapling]:
    config = Config()
    compartido = Motor(config)
    salida = []
    for ident in config.tiendas:
        clase = type(f"Adaptador_{ident}", (AdaptadorScrapling,), {"tienda": ident, "selectores": _selectores(ident)})
        salida.append(clase(contexto, compartido, _respaldo_de(ident)))
    return salida


class MotorScraper:
    def __init__(self, contexto: ScraperContext | None = None) -> None:
        self.scrapers = adaptadores_motor(contexto)

    def discover(self) -> list[dict[str, Any]]:
        if not any(disponible().values()):
            logger.warning("Scrapling no disponible; todos los adaptadores caerán al respaldo.")
        return run_scrapers_parallel(self.scrapers, max_workers=_entero("SCRAPLING_CONCURRENCIA", 4, 1, 8))

    def estado(self) -> dict[str, Any]:
        return {s.store: s.last_status for s in self.scrapers}
