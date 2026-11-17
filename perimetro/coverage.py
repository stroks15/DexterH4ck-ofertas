# perimetro/coverage.py
from __future__ import annotations

from typing import Any

from .registry import recargar


def huecos() -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []
    for t in recargar():
        faltas = []
        if not t.busqueda:
            faltas.append("sin_url_busqueda")
        if not t.selectores.get("card") and not t.vtex_host and not t.sfcc_site:
            faltas.append("sin_selector_ni_catalogo")
        if t.requiere_js and not t.selectores.get("card"):
            faltas.append("js_sin_card")
        if faltas:
            salida.append({"id": t.id, "nombre": t.nombre, "faltas": faltas})
    return salida


def resumen() -> dict[str, Any]:
    tiendas = recargar()
    por_motor: dict[str, int] = {}
    for t in tiendas:
        por_motor[t.estrategias[0]] = por_motor.get(t.estrategias[0], 0) + 1
    return {"tiendas": len(tiendas), "por_motor_primario": por_motor, "con_vtex": sum(1 for t in tiendas if t.vtex_host), "con_sfcc": sum(1 for t in tiendas if t.sfcc_site), "requieren_js": sum(1 for t in tiendas if t.requiere_js), "huecos": huecos()}
