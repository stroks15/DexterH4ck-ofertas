from __future__ import annotations
import argparse,json
from .adaptadores import AdaptadorTienda
from .registry import TIENDAS,seleccionar

def main(argv=None):
 p=argparse.ArgumentParser(prog="perimetro"); sub=p.add_subparsers(dest="cmd",required=True)
 sub.add_parser("doctor"); probe=sub.add_parser("probe");probe.add_argument("--tienda",required=True);probe.add_argument("--q",default="liquidacion")
 crawl=sub.add_parser("crawl");crawl.add_argument("--q",default="liquidacion");sub.add_parser("report")
 a=p.parse_args(argv)
 if a.cmd=="doctor":print(json.dumps({"stores":len(TIENDAS),"dependency_mode":"optional","status":"ok"}));return 0
 if a.cmd=="report":print(json.dumps([{"id":t.id,"nombre":t.nombre,"estrategias":t.estrategias} for t in TIENDAS],ensure_ascii=False));return 0
 targets=seleccionar(a.tienda) if a.cmd=="probe" else TIENDAS
 if not targets:print(json.dumps({"state":"not_configured"}));return 0
 all_rows=[]
 for t in targets:
  ad=AdaptadorTienda(t);rows=ad.discover(a.q);all_rows.extend(rows);print(json.dumps({"tienda":t.id,"status":ad.last_status,"rows":len(rows)},ensure_ascii=False))
 return 0
