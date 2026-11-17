# perimetro/transporte.py
# Único punto de salida a red. Orden: http -> stealth -> browser.
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from . import captcha, stealth, waf
from .proxies import PoolProxies
from .ritmo import Ritmo

try:
    from core.source_resilience import classify_status  # type: ignore
except ImportError:  # pragma: no cover
    from .waf import classify_status  # type: ignore

ESTADOS_REINTENTO = {"timeout", "server_error", "network_error", "rate_limited", "redirect"}
ESTADOS_CORTE = {"blocked_waf", "captcha_required", "forbidden", "unauthorized", "not_found"}

try:
    from scrapling.fetchers import Fetcher as _Fetcher  # type: ignore
except Exception:  # pragma: no cover
    _Fetcher = None
try:
    from scrapling.fetchers import StealthyFetcher as _StealthyFetcher  # type: ignore
except Exception:  # pragma: no cover
    _StealthyFetcher = None
try:
    from patchright.sync_api import sync_playwright as _patchright  # type: ignore
except Exception:  # pragma: no cover
    _patchright = None


def disponibles() -> dict[str, bool]:
    return {"scrapling_http": _Fetcher is not None, "scrapling_stealth": _StealthyFetcher is not None, "patchright": _patchright is not None}


@dataclass
class Respuesta:
    ok: bool
    estado: str
    http: int
    texto: str = ""
    url_final: str = ""
    motor: str = ""
    waf: str = ""
    ms: int = 0
    intentos: int = 0
    detalle: str = ""
    cabeceras: dict[str, str] = field(default_factory=dict)

    @property
    def bloqueada(self) -> bool:
        return self.estado in ESTADOS_CORTE


def _texto(respuesta: Any) -> str:
    for atributo in ("body", "html_content", "text"):
        valor = getattr(respuesta, atributo, None)
        if isinstance(valor, bytes):
            return valor.decode("utf-8", "replace")
        if isinstance(valor, str):
            return valor
    return str(respuesta or "")


def _cabeceras(respuesta: Any) -> dict[str, str]:
    try:
        return {str(k): str(v) for k, v in dict(getattr(respuesta, "headers", None) or {}).items()}
    except Exception:
        return {}


class Transporte:
    def __init__(self, proxies: PoolProxies | None = None, ritmo: Ritmo | None = None) -> None:
        self.proxies = proxies or PoolProxies()
        self.ritmo = ritmo or Ritmo()
        self.cooldown = waf.Cooldown()
        self.intentos_max = int(os.environ.get("PERIMETRO_INTENTOS", "2") or 2)
        self.motor = (os.environ.get("PERIMETRO_MOTOR", "auto") or "auto").strip().lower()

    def get(self, url: str, *, motor: str = "http", referer: str = "", espera_selector: str = "", forzar_cooldown: bool = False, extra: dict[str, str] | None = None) -> Respuesta:
        host = url.split("//", 1)[-1].split("/", 1)[0]
        enfriado = self.cooldown.activo(host)
        if enfriado and not forzar_cooldown:
            return Respuesta(False, "cooldown", 0, detalle=f"host en enfriamiento por {enfriado.get('motivo')}")

        orden = self._orden(motor)
        if not orden:
            return Respuesta(False, "sin_navegador", 0, detalle="ningún motor disponible")

        ultima = Respuesta(False, "sin_intento", 0)
        for intento in range(self.intentos_max + 1):
            for candidato in orden:
                respuesta = self._ejecutar(candidato, url, referer, espera_selector, extra)
                respuesta.intentos = intento + 1
                ultima = respuesta
                if respuesta.ok:
                    self.cooldown.limpiar(host)
                    return respuesta
                if respuesta.estado in ESTADOS_CORTE:
                    if respuesta.estado in ("blocked_waf", "captcha_required"):
                        self.cooldown.registrar(host, respuesta.estado, respuesta.waf)
                    return respuesta
            if intento < self.intentos_max:
                time.sleep(self.ritmo.backoff(intento))
        return ultima

    def _orden(self, motor: str) -> list[str]:
        pedido = self.motor if self.motor != "auto" else motor
        if pedido == "http":
            return ["http_scrapling", "http_requests"] if _Fetcher else ["http_requests"]
        if pedido == "stealth":
            cola = ["stealth"] if _StealthyFetcher else []
            return cola + (["browser"] if _patchright else []) + ["http_requests"]
        if pedido == "browser":
            return (["browser"] if _patchright else []) + ["http_requests"]
        return ["http_requests"]

    def _proxy(self, host: str) -> dict[str, str] | None:
        return self.proxies.elegir(host)

    def _ejecutar(self, candidato: str, url: str, referer: str, espera_selector: str, extra: dict | None) -> Respuesta:
        host = url.split("//", 1)[-1].split("/", 1)[0]
        self.ritmo.esperar(host)
        proxy = self._proxy(host)
        inicio = time.monotonic()
        try:
            if candidato == "http_scrapling":
                crudo = _Fetcher.get(url, timeout=30, retries=1, proxy=proxy, **_limpiar(stealth.kwargs_http(), url, referer, extra))
            elif candidato == "http_requests":
                crudo = requests.get(url, headers=stealth.headers({"Referer": referer} if referer else extra), timeout=25, proxies=({"http": proxy["server"], "https": proxy["server"]} if proxy and not proxy.get("username") else None))
            elif candidato == "stealth":
                crudo = _StealthyFetcher.fetch(url, proxy=proxy, **stealth.kwargs_stealth(espera_selector=espera_selector))
            elif candidato == "browser":
                crudo = self._navegador(url, proxy, espera_selector)
            else:
                return Respuesta(False, "sin_navegador", 0)
        except requests.Timeout:
            return Respuesta(False, "timeout", 0, motor=candidato, ms=_ms(inicio))
        except Exception as exc:
            nombre = type(exc).__name__
            estado = "timeout" if "timeout" in nombre.lower() else "network_error"
            if proxy:
                self.proxies.castigar(proxy)
            return Respuesta(False, estado, 0, motor=candidato, ms=_ms(inicio), detalle=nombre)

        codigo = int(getattr(crudo, "status", getattr(crudo, "status_code", 0)) or 0)
        texto = _texto(crudo)
        cabeceras = _cabeceras(crudo)
        estado = waf.clasificar(codigo, cabeceras, texto)
        proveedor = waf.waf_de(cabeceras, texto)

        if estado == "captcha_required" and captcha.configurado():
            resuelto = captcha.resolver(texto, url)
            if resuelto.get("token"):
                estado = "captcha_resuelto"

        if estado == "rate_limited":
            self.ritmo.penalizar(host, 20.0)
        if proxy and estado in ESTADOS_CORTE:
            self.proxies.castigar(proxy, 60.0)

        return Respuesta(ok=estado in ("ok", "captcha_resuelto"), estado="ok" if estado == "captcha_resuelto" else estado, http=codigo, texto=texto, url_final=str(getattr(crudo, "url", url) or url), motor=candidato, waf=proveedor, ms=_ms(inicio), cabeceras=cabeceras)

    def _navegador(self, url: str, proxy: dict | None, espera_selector: str) -> Any:
        with _patchright() as pw:
            lanzamiento: dict[str, Any] = {"headless": True}
            if proxy:
                lanzamiento["proxy"] = proxy
            navegador = pw.chromium.launch(**lanzamiento)
            contexto = navegador.new_context(locale="es-MX", timezone_id="America/Mexico_City", viewport={"width": 1440, "height": 900}, user_agent=stealth.headers()["User-Agent"])
            contexto.add_init_script(stealth.SCRIPT_EVASION)
            pagina = contexto.new_page()
            pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
            if espera_selector:
                try:
                    pagina.wait_for_selector(espera_selector, timeout=15000)
                except Exception:
                    pass
            time.sleep(stealth.humano())
            html = pagina.content()
            navegador.close()
            return _Crudo(200, html, {}, url)


class _Crudo:
    def __init__(self, status: int, texto: str, cabeceras: dict, url: str) -> None:
        self.status = status
        self.text = texto
        self.headers = cabeceras
        self.url = url


def _ms(inicio: float) -> int:
    return int((time.monotonic() - inicio) * 1000)


def _limpiar(opciones: dict, url: str, referer: str, extra: dict | None) -> dict:
    salida = {k: v for k, v in opciones.items() if v not in (None, "", False)}
    cabeceras = dict(salida.pop("headers", {}) or {})
    if referer:
        cabeceras["Referer"] = referer
    if extra:
        cabeceras.update({str(k): str(v) for k, v in extra.items()})
    if cabeceras:
        salida["headers"] = cabeceras
    return salida
