# perimetro/cli.py
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, transporte as capa_transporte
from .adaptadores import AdaptadorTienda
from .proxies import PoolProxies
from .registry import recargar, seleccionar, tiendas_activas
from .ritmo import Ritmo
from .transporte import Transporte
from .waf import Cooldown


def _transporte() -> Transporte:
    return Transporte(PoolProxies(), Ritmo())


def cmd_doctor(_: argparse.Namespace) -> int:
    print(f"perimetro {__version__}")
    for nombre, activo in capa_transporte.disponibles().items():
        print(f"  {nombre:22} {'OK' if activo else 'AUSENTE'}")
    print(f"  proxies                {PoolProxies().describir()}")
    print(f"  hosts en cooldown      {len(Cooldown().listar())}")
    print("\ntiendas:")
    for t in tiendas_activas():
        print(f"  {t.id:12} {'JS' if t.requiere_js else '--'} {t.prioridad:2} {'/'.join(t.estrategias)}")
    print(f"\ntotal: {len(tiendas_activas())} activas de {len(recargar())}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    objetivo = seleccionar(args.tienda)
    if not objetivo:
        print("tienda no encontrada")
        return 2
    transporte = _transporte()
    from . import extraccion

    for t in objetivo:
        respuesta = transporte.get(t.url_busqueda(args.q) or t.home, motor="stealth" if t.requiere_js else "http", forzar_cooldown=args.forzar)
        print(f"\n[{t.id}] {respuesta.estado} http={respuesta.http} motor={respuesta.motor} waf={respuesta.waf or '-'} bytes={len(respuesta.texto)} ms={respuesta.ms}")
        if respuesta.ok and respuesta.texto:
            filas = []
            if respuesta.texto.lstrip().startswith("["):
                try:
                    filas = extraccion.vtex(json.loads(respuesta.texto), t.nombre, t.home)
                except ValueError:
                    filas = []
            filas = filas or extraccion.jsonld(respuesta.texto, t.nombre, t.home) or extraccion.selectores(respuesta.texto, t.nombre, t.home, t.selectores)
            print(f"  filas extraídas: {len(filas)}")
            for fila in filas[:3]:
                print(f"  - {fila['precio_actual']:>9.2f} | desc {fila['descuento']:>2}% | {fila['titulo'][:70]}")
            if args.guardar:
                Path(f"probe_{t.id}.html").write_text(respuesta.texto, encoding="utf-8")
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    objetivo = seleccionar(args.tiendas)
    if not objetivo:
        print("sin tiendas seleccionadas")
        return 2
    transporte = _transporte()
    filas: list[dict] = []
    estados: dict[str, dict] = {}
    for t in objetivo:
        adaptador = AdaptadorTienda(t, None, transporte)
        filas.extend(adaptador.discover())
        estados[t.id] = adaptador.last_status
    from .extraccion import deduplicar

    filas = deduplicar(filas)
    if args.out:
        Path(args.out).write_text(json.dumps({"total": len(filas), "estados": estados, "ofertas": filas}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(filas)} ofertas -> {args.out}")
    else:
        print(json.dumps({"total": len(filas), "estados": estados}, ensure_ascii=False, indent=2))
    return 0


def cmd_report(_: argparse.Namespace) -> int:
    enfriados = Cooldown().listar()
    print(f"hosts en enfriamiento: {len(enfriados)}")
    for host, datos in enfriados.items():
        print(f"  {host:40} {datos.get('motivo')} {datos.get('waf') or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="perimetro", description="Perímetro de tiendas con transporte stealth")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("doctor", help="estado de dependencias, proxies y tiendas").set_defaults(func=cmd_doctor)
    probe = sub.add_parser("probe", help="una petición de prueba y conteo de extracción")
    probe.add_argument("--tienda", nargs="*", default=["todas"])
    probe.add_argument("--q", default="liquidacion")
    probe.add_argument("--forzar", action="store_true")
    probe.add_argument("--guardar", action="store_true")
    probe.set_defaults(func=cmd_probe)
    crawl = sub.add_parser("crawl", help="recorre tiendas y escribe ofertas")
    crawl.add_argument("--tiendas", nargs="*", default=["todas"])
    crawl.add_argument("--q", default="")
    crawl.add_argument("--max", type=int, default=40)
    crawl.add_argument("--out", default="")
    crawl.set_defaults(func=cmd_crawl)
    sub.add_parser("report", help="hosts en cooldown").set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    if getattr(args, "q", ""):
        os.environ["PERIMETRO_QUERIES"] = args.q
    if hasattr(args, "max"):
        os.environ.setdefault("PERIMETRO_MAX_POR_TIENDA", str(args.max))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
