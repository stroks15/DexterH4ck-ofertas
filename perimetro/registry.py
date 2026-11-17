# perimetro/registry.py
# Registro del perímetro. La data vive en config/perimetro_tiendas.json para poder
# ajustar una tienda sin tocar código; aquí solo se valida y se expone tipada.
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

RAIZ = Path(__file__).resolve().parent.parent
RUTA_DEFECTO = RAIZ / "config" / "perimetro_tiendas.json"

ESTRATEGIAS_VALIDAS = {"vtex", "sfcc", "nextdata", "graphql", "amazon", "jsonld", "html"}


@dataclass(frozen=True)
class Tienda:
    id: str
    nombre: str
    home: str
    estrategias: tuple[str, ...]
    busqueda: str = ""
    vtex_host: str = ""
    sfcc_site: str = ""
    requiere_js: bool = False
    prioridad: int = 5
    notas: str = ""
    selectores: dict[str, str] = field(default_factory=dict)
    activa: bool = True

    def url_busqueda(self, termino: str) -> str:
        if not self.busqueda:
            return ""
        return self.busqueda.replace("{q}", quote_plus(termino))

    @property
    def host(self) -> str:
        partes = self.home.split("//", 1)
        return partes[1].split("/", 1)[0] if len(partes) == 2 else self.home

    def a_dict(self) -> dict[str, Any]:
        return {"id": self.id, "nombre": self.nombre, "home": self.home, "estrategias": list(self.estrategias), "requiere_js": self.requiere_js, "prioridad": self.prioridad, "activa": self.activa}


def _ruta() -> Path:
    return Path(os.environ.get("PERIMETRO_CONFIG", "") or RUTA_DEFECTO)


def _normalizar_estrategias(bruto: Any) -> tuple[str, ...]:
    if isinstance(bruto, str):
        bruto = [bruto]
    if not isinstance(bruto, list):
        return ("html",)
    limpias = [str(e).strip().lower() for e in bruto if str(e).strip()]
    validas = tuple(e for e in limpias if e in ESTRATEGIAS_VALIDAS)
    return validas or ("html",)


def _tienda_desde(bruto: dict[str, Any]) -> Tienda | None:
    ident = str(bruto.get("id") or "").strip().lower()
    nombre = str(bruto.get("nombre") or "").strip()
    home = str(bruto.get("home") or "").strip()
    if not ident or not nombre or not home.startswith("http"):
        return None
    selectores = bruto.get("selectores") or {}
    if not isinstance(selectores, dict):
        selectores = {}
    try:
        prioridad = int(bruto.get("prioridad", 5))
    except (TypeError, ValueError):
        prioridad = 5
    return Tienda(
        id=ident,
        nombre=nombre,
        home=home,
        estrategias=_normalizar_estrategias(bruto.get("estrategias")),
        busqueda=str(bruto.get("busqueda") or ""),
        vtex_host=str(bruto.get("vtex_host") or "").rstrip("/"),
        sfcc_site=str(bruto.get("sfcc_site") or ""),
        requiere_js=bool(bruto.get("requiere_js")),
        prioridad=max(1, min(10, prioridad)),
        notas=str(bruto.get("notas") or ""),
        selectores={str(k): str(v) for k, v in selectores.items() if v},
        activa=bruto.get("activa", True) is not False,
    )


@lru_cache(maxsize=4)
def _cargar(ruta_txt: str) -> tuple[Tienda, ...]:
    ruta = Path(ruta_txt)
    if not ruta.is_file():
        return ()
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    tiendas: dict[str, Tienda] = {}
    for bruto in data.get("tiendas") or []:
        if not isinstance(bruto, dict):
            continue
        leida = _tienda_desde(bruto)
        if leida and leida.id not in tiendas:
            tiendas[leida.id] = leida
    return tuple(sorted(tiendas.values(), key=lambda t: (-t.prioridad, t.id)))


def recargar() -> tuple[Tienda, ...]:
    _cargar.cache_clear()
    return _cargar(str(_ruta()))


TIENDAS: tuple[Tienda, ...] = _cargar(str(_ruta()))
TIENDAS_OBJETIVO: tuple[str, ...] = tuple(t.nombre for t in TIENDAS)


def tiendas_activas() -> list[Tienda]:
    return [t for t in recargar() if t.activa]


def tienda(ident: str) -> Tienda | None:
    clave = str(ident or "").strip().lower()
    if not clave:
        return None
    for t in recargar():
        if t.id == clave or t.nombre.lower() == clave:
            return t
    return None


def seleccionar(nombres: list[str] | None) -> list[Tienda]:
    activas = tiendas_activas()
    if not nombres:
        return activas
    pedidos = [n.strip().lower() for n in nombres if n.strip()]
    if not pedidos or "todas" in pedidos or "all" in pedidos:
        return activas
    indice = {t.id: t for t in activas}
    salida: list[Tienda] = []
    for pedido in pedidos:
        encontrada = indice.get(pedido) or tienda(pedido)
        if encontrada and encontrada.activa and encontrada not in salida:
            salida.append(encontrada)
    return salida
