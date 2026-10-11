# scrapers/discovery_utils.py
"""Utilidades compartidas para descubrir más liquidaciones por tienda (términos y paginación)."""
from __future__ import annotations

import os
from typing import Any, Callable, Iterable

from core.source_resilience import BLOCKING_STATES

# Estados tras los cuales NO se insiste con más términos/páginas contra la misma fuente.
ESTADOS_CORTE = set(BLOCKING_STATES) | {"not_configured", "unauthorized", "not_found"}

# Términos de búsqueda orientados a liquidación (sobrescribibles por variable de entorno).
TERMINOS_LIQUIDACION = (
    "liquidacion", "remate", "outlet", "rebajas",
    "ultimas piezas", "precio final", "mega rebajas", "descuento",
)


def env_int(nombre: str, defecto: int, minimo: int = 1, maximo: int = 10) -> int:
    """Entero desde entorno; vacío o inválido => defecto (nunca lanza)."""
    try:
        valor = int(os.environ.get(nombre, "").strip() or defecto)
    except (TypeError, ValueError):
        valor = defecto
    return max(minimo, min(maximo, valor))


def env_terminos(nombre: str, defecto: Iterable[str]) -> tuple[str, ...]:
    crudo = os.environ.get(nombre, "")
    terminos = tuple(t.strip() for t in crudo.split(",") if t.strip())
    return terminos or tuple(defecto)


def contar_filas(payload: Any) -> int:
    """Cuenta productos crudos de una respuesta GraphQL de búsqueda (0 si no reconoce el formato)."""
    try:
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        search = data.get("search") or data.get("searchAndFilter") or data.get("SearchAndFilter") or {}
        filas = search.get("products") if isinstance(search, dict) else None
        if not isinstance(filas, list):
            filas = data.get("products")
        return len(filas) if isinstance(filas, list) else 0
    except AttributeError:
        return 0


def buscar_graphql_paginado(
    consultar: Callable[..., dict],
    parser: Any,
    terminos: Iterable[str],
    paginas: int,
    tamano: int,
    estado: dict,
) -> list[dict]:
    """Recorre términos y páginas, deduplica y se detiene ante bloqueo/no disponibilidad.

    Solo pide la página siguiente si la actual vino llena. Un bloqueo (403/429/5xx) corta
    toda la fuente en este ciclo; un timeout/respuesta inválida solo salta al siguiente término.
    """
    vistos: set = set()
    resultados: list[dict] = []
    for termino in terminos:
        for pagina in range(1, paginas + 1):
            payload = consultar(termino, pagina=pagina, tamano=tamano)
            situacion = estado.get("state")
            if situacion in ESTADOS_CORTE:
                return resultados
            if situacion != "ok":
                break
            for item in parser.parsear_respuesta_busqueda(payload):
                clave = item.get("id") or item.get("url")
                if clave in vistos:
                    continue
                vistos.add(clave)
                resultados.append(item)
            if contar_filas(payload) < tamano:
                break
    return resultados
