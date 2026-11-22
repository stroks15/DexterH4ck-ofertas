"""Motor HTTP primario opcional con degradación a respaldo por tienda.

La política respeta WAF/CAPTCHA: no falsifica fingerprints ni automatiza retos.
"""
from __future__ import annotations
import asyncio, json, os, threading, time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
try:
    from scrapling import Selector as _Selector
    SCRAPLING_AVAILABLE=True
except Exception:
    _Selector=None; SCRAPLING_AVAILABLE=False
try:
    from perimetro.extraccion import extraer as _extraer
    from perimetro.transporte import Transporte
    from perimetro.waf import Cooldown
    _PERIMETRO_AVAILABLE=True
except Exception:
    _extraer=None; Transporte=None; Cooldown=None; _PERIMETRO_AVAILABLE=False

@dataclass
class Perfil:
    user_agent:str="DexterH4ck-ofertas/2.0 (+deal-monitor; es-MX)"
    sec_ch_ua:str=""
    plataforma:str=""
    viewport:dict=field(default_factory=dict)
    dpr:float=1.0
    impersonate:str=""
    movil:bool=False

@dataclass
class Presupuesto:
    base_ms:int=12000; min_ms:int=5000; max_ms:int=30000; factor_ewma:float=2.5; alfa_ewma:float=0.3; tope_retry_after_s:int=45
    ewma:float=4.0
    def timeout(self,retry_after=None):
        v=max(self.min_ms,min(self.max_ms,int(max(self.base_ms,self.ewma*1000*self.factor_ewma))))
        if retry_after:
            try:v=max(v,min(self.max_ms,int(float(retry_after)*1000)))
            except (TypeError,ValueError):pass
        return v/1000
    def update(self,latency):self.ewma=self.alfa_ewma*max(0,float(latency))+(1-self.alfa_ewma)*self.ewma

@dataclass
class Config:
    concurrencia:int=4; presupuesto:Presupuesto=field(default_factory=Presupuesto)
    @classmethod
    def load(cls):
        try:d=json.loads((Path(__file__).resolve().parents[1]/"config/scrapling_motor.json").read_text()); p=d.get("presupuesto",{}); return cls(int(d.get("concurrencia",4)),Presupuesto(**{k:v for k,v in p.items() if k in Presupuesto.__dataclass_fields__}))
        except Exception:return cls()

class Rotador:
    def __init__(self,proxies=None,rotate_every=10):
        from perimetro.proxies import Proxies
        self.pool=Proxies(proxies);self.rotate_every=max(1,rotate_every);self.n={};self.sticky={}
    def next(self,host):
        n=self.n.get(host,0);self.n[host]=n+1
        if host not in self.sticky or n%self.rotate_every==0:self.sticky[host]=self.pool.get(host)
        return self.sticky[host]

@dataclass
class Resultado:
    filas:list=field(default_factory=list); estado:str="sin_motor"; fallback:bool=False; motivo:str=""

class Motor:
    def __init__(self,config=None,transporte=None):
        self.config=config or Config.load(); self._sem=asyncio.Semaphore(max(1,self.config.concurrencia)); self.transporte=transporte or (Transporte(timeout=self.config.presupuesto.timeout()) if _PERIMETRO_AVAILABLE else None)
    async def pedir(self,url,tienda,q="liquidacion"):
        if not _PERIMETRO_AVAILABLE or self.transporte is None:return Resultado(estado="sin_motor")
        async with self._sem:
            start=time.monotonic()
            try:r=await asyncio.to_thread(self.transporte.pedir,url);self.config.presupuesto.update(time.monotonic()-start)
            except Exception as exc:return Resultado(estado="invalid_response",motivo=type(exc).__name__)
        if r["state"]!="ok":return Resultado(estado=r["state"],motivo=str(r.get("status") or ""))
        try:
            from perimetro.registry import seleccionar
            t=next((x for x in seleccionar() if x.id==tienda or x.nombre.casefold()==tienda.casefold()),None)
            if not t:return Resultado(estado="not_configured")
            rows=[]
            for strategy in t.estrategias:
                rows=_extraer(strategy,r["body"],t.nombre,r["url"],t.selectores)
                if rows:return Resultado(rows,"ok")
            return Resultado([],"empty")
        except Exception as exc:return Resultado(estado="invalid_response",motivo=type(exc).__name__)
    def pedir_sync(self,url,tienda,q="liquidacion"):
        """Ruta síncrona del pipeline legado; no crea event loops auxiliares."""
        if not SCRAPLING_AVAILABLE or not _PERIMETRO_AVAILABLE or self.transporte is None:
            return Resultado(estado="sin_motor")
        start=time.monotonic()
        try:
            response=self.transporte.pedir(url)
            self.config.presupuesto.update(time.monotonic()-start)
        except Exception as exc:
            return Resultado(estado="invalid_response", motivo=type(exc).__name__)
        if response.get("state") != "ok":
            return Resultado(estado=response.get("state", "invalid_response"), motivo=str(response.get("status") or ""))
        try:
            from perimetro.registry import seleccionar
            target=next((x for x in seleccionar() if x.id==tienda or x.nombre.casefold()==tienda.casefold()),None)
            if not target:return Resultado(estado="not_configured")
            for strategy in target.estrategias:
                rows=_extraer(strategy,response["body"],target.nombre,response["url"],target.selectores)
                if rows:return Resultado(rows,"ok")
            return Resultado(estado="empty")
        except Exception as exc:
            return Resultado(estado="invalid_response", motivo=type(exc).__name__)
    async def pedir_o_fallback(self,url,tienda,fallback,q="liquidacion"):
        result=await self.pedir(url,tienda,q)
        if result.filas:return result
        try:rows=await asyncio.to_thread(fallback)
        except Exception:rows=[]
        result.filas=rows or [];result.fallback=True
        return result
    def pedir_o_fallback_sync(self,url,tienda,fallback,q="liquidacion"):
        result=self.pedir_sync(url,tienda,q)
        if result.filas:return result
        try:result.filas=fallback() or []
        except Exception:result.filas=[]
        result.fallback=True;return result

def extraer_minimo(html,selectores,base_url="",tienda=""):
    if SCRAPLING_AVAILABLE:
        try:
            selector=_Selector(html)
            cards=selector.css(selectores.get("card",""))
            if cards:
                return _extraer("html",html,tienda,base_url,selectores) if _PERIMETRO_AVAILABLE else []
        except Exception:pass
    return _extraer("html",html,tienda,base_url,selectores) if _PERIMETRO_AVAILABLE else []

__all__=["Perfil","Config","Presupuesto","Rotador","Cooldown","Resultado","Motor","extraer_minimo","SCRAPLING_AVAILABLE"]
