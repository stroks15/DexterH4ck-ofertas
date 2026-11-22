from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID = {"vtex", "sfcc", "nextdata", "graphql", "amazon", "jsonld", "html"}
PATH = Path(__file__).resolve().parents[1] / "config" / "perimetro_tiendas.json"

@dataclass(frozen=True)
class Tienda:
    id: str
    nombre: str
    home: str
    busqueda: str
    estrategias: tuple[str, ...]
    requiere_js: bool = False
    prioridad: int = 100
    selectores: dict[str, str] | None = None
    notas: str = ""
    vtex_host: str = ""
    sfcc_site: str = ""

    def url(self, q: str) -> str:
        from urllib.parse import quote_plus
        return self.busqueda.format(q=quote_plus(str(q)))

def _load() -> tuple[Tienda, ...]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    result = []
    for row in data.get("tiendas", []):
        if not isinstance(row, dict) or not row.get("id") or not row.get("nombre") or "{q}" not in row.get("busqueda", ""):
            continue
        strategies = tuple(x for x in row.get("estrategias", []) if x in VALID)
        result.append(Tienda(id=row["id"], nombre=row["nombre"], home=row.get("home", ""), busqueda=row["busqueda"], estrategias=strategies, requiere_js=bool(row.get("requiere_js")), prioridad=int(row.get("prioridad", 100)), selectores=row.get("selectores") or {}, notas=row.get("notas", ""), vtex_host=row.get("vtex_host", ""), sfcc_site=row.get("sfcc_site", "")))
    return tuple(sorted(result, key=lambda t: t.prioridad))

TIENDAS = _load()
TIENDAS_OBJETIVO = tuple(t for t in TIENDAS if t.id != "mercado-libre-mx")

def seleccionar(nombre: str | None = None) -> tuple[Tienda, ...]:
    if not nombre:
        return TIENDAS_OBJETIVO
    value = str(nombre).strip().casefold()
    return tuple(t for t in TIENDAS_OBJETIVO if value in t.id.casefold() or value in t.nombre.casefold())

__all__ = ["Tienda", "TIENDAS", "TIENDAS_OBJETIVO", "seleccionar"]
