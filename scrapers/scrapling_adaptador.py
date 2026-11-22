from __future__ import annotations
from core.scraper_base import BaseScraper, ScraperContext
from core.scrapling_motor import Motor
from perimetro.registry import seleccionar

class AdaptadorScrapling(BaseScraper):
    """Motor primario; respaldo lazy de la tienda API existente ante error o vacío."""
    def __init__(self,store,context:ScraperContext|None=None):
        super().__init__(context);self.store=store;self.last_status={"state":"not_run"};self.motor=Motor()
        self.tienda=next((t for t in seleccionar() if t.nombre.casefold()==store.casefold() or t.id==store),None)
    def _fallback(self):
        from scrapers.api_stores import api_first_scrapers
        # Excluir motor evita que un fallback vuelva a entrar al mismo motor.
        for scraper in api_first_scrapers(self.context, include_motor=False):
            if scraper is self:continue
            if str(getattr(scraper,"store","")).casefold()==self.store.casefold():
                return scraper.discover() or [],getattr(scraper,"last_status",{})
        # Algunas tiendas solo tienen fuente HTML legacy: el pipeline global ya la conserva.
        return [],{"state":"not_configured"}
    def discover(self):
        try:
            if not self.tienda:self.last_status={"state":"not_configured"};return []
            q="liquidacion";url=self.tienda.url(q)
            fallback_status = {}
            def fallback():
                nonlocal fallback_status
                rows, fallback_status = self._fallback()
                return rows
            result=self.motor.pedir_o_fallback_sync(url,self.store,fallback,q)
            state = result.estado
            if result.fallback and result.filas:
                state = "ok"
            elif result.fallback:
                state = str(fallback_status.get("state") or state)
                # El motor opcional no debe convertir una fuente no configurada
                # en `empty`: el contrato de BaseScraper conserva el motivo real.
                if state == "sin_motor" and fallback_status:
                    state = "not_configured"
            self.last_status={"state":state,"fallback":result.fallback,"count":len(result.filas),"motor_state":result.estado}
            return result.filas or []
        except Exception as exc:self.last_status={"state":"invalid_response","reason":type(exc).__name__};return []

class MotorScraper:
    def __init__(self,context=None):self.adapters=adaptadores_motor(context)
    def discover(self):
        out=[]
        for adapter in self.adapters:
            try:out.extend(adapter.discover())
            except Exception:continue
        return out

def adaptadores_motor(contexto=None):
    return [AdaptadorScrapling(t.nombre,contexto) for t in seleccionar()]
