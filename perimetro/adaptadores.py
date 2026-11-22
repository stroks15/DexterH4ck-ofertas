from __future__ import annotations
from urllib.parse import urlparse
from .extraccion import extraer
from .registry import seleccionar
from .transporte import Transporte

class AdaptadorTienda:
    def __init__(self,tienda,context=None,transporte=None):self.tienda=tienda;self.context=context;self.transporte=transporte or Transporte(timeout=getattr(context,"timeout",12));self.last_status={"state":"not_run"}
    def discover(self,q="liquidacion"):
        try:
            response=self.transporte.pedir(self.tienda.url(q)); state=response["state"]
            if state!="ok":self.last_status={"state":state,"http":response.get("status")};return []
            for strategy in self.tienda.estrategias:
                rows=extraer(strategy,response["body"],self.tienda.nombre,response["url"],self.tienda.selectores)
                if rows:self.last_status={"state":"ok","count":len(rows),"strategy":strategy};return rows
            self.last_status={"state":"empty"};return []
        except Exception as exc:self.last_status={"state":"invalid_response","reason":type(exc).__name__};return []

class PerimetroScraper:
    def __init__(self,tiendas=None,context=None):self.adapters=[AdaptadorTienda(t,context) for t in (tiendas or seleccionar())]
    def discover(self):
        out=[]
        for adapter in self.adapters:
            try:out.extend(adapter.discover())
            except Exception:continue
        return out
