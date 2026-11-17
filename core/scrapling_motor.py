# core/scrapling_motor.py
# Motor de obtención primario basado en Scrapling, con degradación al método actual.
#
# Reglas:
#   1. Scrapling primero; si falla o devuelve 0 filas, se ejecuta el respaldo legacy.
#   2. Un perfil de dispositivo es coherente (UA + sec-ch-ua + plataforma + viewport + DPR).
#   3. El timeout se adapta a la latencia observada por host y respeta Retry-After.
#   4. Un bloqueo cuesta un cooldown de host, no un ciclo completo.
#   5. Solo se parsean nodos de tarjeta; nunca se recorre el DOM completo dos veces.
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger("DexterH4ck.Motor")

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CONFIG = Path(os.environ.get("SCRAPLING_CONFIG", "") or RAIZ / "config" / "scrapling_motor.json")
RUTA_COOLDOWN = Path(os.environ.get("MOTOR_COOLDOWN", "") or RAIZ / "motor_cooldown.json")

ESTADOS_BLOQUEO = {"blocked_waf", "captcha_required", "forbidden", "unauthorized", "rate_limited"}
ESTADOS_REINTENTO = {"timeout", "server_error", "network_error", "sin_motor"}

try:
    from core.source_resilience import classify_status  # type: ignore
except ImportError:  # pragma: no cover
    def classify_status(codigo: int) -> str:  # type: ignore
        if codigo == 200:
            return "ok"
        if codigo == 429:
            return "rate_limited"
        if codigo in (401, 407):
            return "unauthorized"
        if codigo == 403:
            return "forbidden"
        if codigo == 404:
            return "not_found"
        if codigo >= 500:
            return "server_error"
        if 300 <= codigo < 400:
            return "redirect"
        return f"http_{codigo}"

try:
    from perimetro import waf as _waf  # type: ignore
except Exception:  # pragma: no cover
    _waf = None

FIRMAS_WAF = (
    ("captcha_required", ("g-recaptcha", "hcaptcha", "cf-turnstile", "captcha-delivery", "px-captcha")),
    ("blocked_waf", ("just a moment", "checking your browser", "access denied", "_incapsula_resource", "datadome", "x-kpsdk")),
    ("rate_limited", ("too many requests", "rate limit", "exceeded")),
)


def _si(nombre: str, defecto: bool = False) -> bool:
    crudo = os.environ.get(nombre)
    if crudo is None:
        return defecto
    return crudo.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def clasificar(codigo: int, cabeceras: dict[str, str] | None, cuerpo: str) -> str:
    if _waf is not None:
        return _waf.clasificar(codigo, cabeceras, cuerpo)
    base = classify_status(codigo)
    if base != "ok":
        return base
    muestra = (cuerpo or "")[:30000].lower()
    for estado, firmas in FIRMAS_WAF:
        if any(firma in muestra for firma in firmas):
            return estado
    return "ok"


def _env_int(nombre: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        return max(minimo, min(maximo, int(os.environ.get(nombre, "") or defecto)))
    except (TypeError, ValueError):
        return defecto


try:
    from scrapling.fetchers import AsyncFetcherSession as _SesionHttp  # type: ignore
except Exception:  # pragma: no cover
    _SesionHttp = None
try:
    from scrapling.fetchers import AsyncStealthySession as _SesionStealth  # type: ignore
except Exception:  # pragma: no cover
    try:
        from scrapling.fetchers import StealthySession as _SesionStealth  # type: ignore
    except Exception:
        _SesionStealth = None
try:
    from scrapling.parser import Selector as _Selector  # type: ignore
except Exception:  # pragma: no cover
    _Selector = None


def disponible() -> dict[str, bool]:
    return {"http": _SesionHttp is not None, "stealth": _SesionStealth is not None, "selector": _Selector is not None}


@dataclass(frozen=True)
class Perfil:
    id: str
    ua: str
    plataforma: str
    sec_ch_ua: str
    movil: bool
    ancho: int
    alto: int
    dpr: float
    impersonate: str
    dispositivo: str = "desktop"

    def cabeceras(self, referer: str = "") -> dict[str, str]:
        base = {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
            "Sec-Ch-Ua-Mobile": "?1" if self.movil else "?0",
            "Sec-Ch-Ua-Platform": f'"{self.plataforma}"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }
        if self.sec_ch_ua:
            base["Sec-Ch-Ua"] = self.sec_ch_ua
        if referer:
            base["Referer"] = referer
        return base

    def viewport(self) -> dict[str, int]:
        return {"width": self.ancho, "height": self.alto}


def _perfil_desde(bruto: dict) -> Perfil | None:
    try:
        vista = bruto.get("viewport") or {}
        return Perfil(
            id=str(bruto["id"]),
            ua=str(bruto["ua"]),
            plataforma=str(bruto.get("plataforma") or "Windows"),
            sec_ch_ua=str(bruto.get("sec_ch_ua") or ""),
            movil=bool(bruto.get("movil")),
            ancho=int(vista.get("width") or 1366),
            alto=int(vista.get("height") or 768),
            dpr=float(bruto.get("dpr") or 1.0),
            impersonate=str(bruto.get("impersonate") or "chrome"),
            dispositivo=str(bruto.get("dispositivo") or "desktop"),
        )
    except (KeyError, TypeError, ValueError):
        return None


class Config:
    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = Path(ruta or RUTA_CONFIG)
        data: dict[str, Any] = {}
        if self.ruta.is_file():
            try:
                data = json.loads(self.ruta.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("config ilegible (%s); valores por defecto", type(exc).__name__)
        self.data = data if isinstance(data, dict) else {}
        self.perfiles = [p for p in (_perfil_desde(b) for b in self.data.get("perfiles") or [] if isinstance(b, dict)) if p]
        if not self.perfiles:
            self.perfiles = [Perfil("desktop_chrome_win", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36", "Windows", "", False, 1366, 768, 1.0, "chrome")]
        self._pedidos = [p.strip() for p in (os.environ.get("SCRAPLING_PERFILES") or "").split(",") if p.strip()]
        self.presupuesto = self.data.get("presupuesto") or {}
        self.esperas = self.data.get("esperas") or {}
        self.dominios = self.data.get("dominios") or {}
        self.perfil_por_host = self.data.get("perfil_por_host") or {}
        self.tiendas = self.data.get("tiendas") or {}

    @property
    def modo(self) -> str:
        return (os.environ.get("SCRAPLING_MOTOR") or self.data.get("modo") or "auto").strip().lower()

    @property
    def concurrencia(self) -> int:
        return _env_int("SCRAPLING_CONCURRENCIA", int(self.data.get("concurrencia") or 4), 1, 12)

    @property
    def rotar_cada(self) -> int:
        return _env_int("SCRAPLING_ROTAR_CADA", int(self.data.get("rotar_perfil_cada") or 3), 1, 50)

    @property
    def fallback(self) -> bool:
        return _si("SCRAPLING_FALLBACK", bool(self.data.get("fallback_habilitado", True)))

    @property
    def reintentos(self) -> int:
        return _env_int("SCRAPLING_REINTENTOS", int(self.data.get("reintentos") or 2), 0, 4)

    def perfiles_activos(self) -> list[Perfil]:
        if not self._pedidos:
            return self.perfiles
        indice = {p.id: p for p in self.perfiles}
        elegidos = [indice[i] for i in self._pedidos if i in indice]
        return elegidos or self.perfiles

    def perfil_de(self, host: str) -> Perfil:
        activos = self.perfiles_activos()
        for dominio, ident in self.perfil_por_host.items():
            if host.endswith(dominio):
                for perfil in activos:
                    if perfil.id == ident:
                        return perfil
        return activos[0]

    def requiere_navegador(self, host: str) -> bool:
        for dominio in self.dominios.get("nunca_navegador") or []:
            if host.endswith(dominio):
                return False
        return any(host.endswith(d) for d in self.dominios.get("requieren_navegador") or [])

    def resolver_cloudflare(self, host: str) -> bool:
        crudo = (os.environ.get("SCRAPLING_SOLVE_CLOUDFLARE") or "").strip().lower()
        if crudo in ("1", "true", "si", "sí"):
            return True
        if crudo in ("0", "false", "no"):
            return False
        return any(host.endswith(d) for d in self.dominios.get("solve_cloudflare") or [])


class Presupuesto:
    def __init__(self, conf: dict[str, Any]) -> None:
        self.base = float(conf.get("base_ms") or 20000)
        self.minimo = float(conf.get("min_ms") or 8000)
        self.maximo = float(conf.get("max_ms") or 60000)
        self.factor = float(conf.get("factor_ewma") or 1.8)
        self.alfa = float(conf.get("alfa_ewma") or 0.3)
        self.tope_retry = float(conf.get("tope_retry_after_s") or 45)
        self._ewma: dict[str, float] = {}
        self._lock = threading.Lock()

    def timeout(self, host: str) -> float:
        with self._lock:
            referencia = self._ewma.get(host)
        if not referencia:
            return self.base
        return max(self.minimo, min(self.maximo, referencia * self.factor))

    def observar(self, host: str, ms: float) -> None:
        with self._lock:
            previo = self._ewma.get(host, ms)
            self._ewma[host] = (1 - self.alfa) * previo + self.alfa * ms

    def espera_retry_after(self, valor: str | None) -> float:
        if not valor:
            return 0.0
        try:
            return min(self.tope_retry, float(str(valor).strip().rstrip("s")))
        except ValueError:
            return 0.0


class Rotador:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._contador: dict[str, int] = {}
        self._asignado: dict[str, Perfil] = {}
        self._lock = threading.Lock()

    def siguiente(self, host: str) -> Perfil:
        with self._lock:
            actual = self._asignado.get(host)
            cuenta = self._contador.get(host, 0) + 1
            self._contador[host] = cuenta
            if actual is None or cuenta % self.config.rotar_cada == 0:
                activos = self.config.perfiles_activos()
                actual = self.config.perfil_de(host)
                otros = [p for p in activos if p.id != actual.id]
                if otros and cuenta > 1:
                    actual = random.choice(otros)
                self._asignado[host] = actual
            return actual


class Cooldown:
    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = Path(ruta or RUTA_COOLDOWN)
        try:
            self.minutos = max(1, int(os.environ.get("MOTOR_COOLDOWN_MIN", "") or 30))
        except (TypeError, ValueError):
            self.minutos = 30

    def _leer(self) -> dict[str, dict]:
        try:
            data = json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def activo(self, host: str) -> dict | None:
        entrada = self._leer().get(host)
        if entrada and float(entrada.get("hasta", 0)) > time.time():
            return entrada
        return None

    def registrar(self, host: str, motivo: str) -> None:
        data = self._leer()
        data[host] = {"motivo": motivo, "hasta": time.time() + self.minutos * 60}
        try:
            self.ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def limpiar(self, host: str) -> None:
        data = self._leer()
        if data.pop(host, None) is not None:
            try:
                self.ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass


@dataclass
class Resultado:
    ok: bool = False
    estado: str = "sin_intento"
    http: int = 0
    texto: str = ""
    filas: list[dict] = field(default_factory=list)
    motor: str = ""
    perfil: str = ""
    fallback: bool = False
    ms: int = 0
    detalle: str = ""

    @property
    def bloqueado(self) -> bool:
        return self.estado in ESTADOS_BLOQUEO


def _host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0].lower()


class Motor:
    def __init__(self, config: Config | None = None, proxies: list[dict] | None = None) -> None:
        self.config = config or Config()
        self.presupuesto = Presupuesto(self.config.presupuesto)
        self.rotador = Rotador(self.config)
        self.cooldown = Cooldown()
        self.proxies = proxies if proxies is not None else self._proxies_entorno()
        self._turno = 0
        self._semaforo: asyncio.Semaphore | None = None
        self._loop: Any = None
        self.ultimo: dict[str, Resultado] = {}

    @staticmethod
    def _proxies_entorno() -> list[dict]:
        crudo = (os.environ.get("PROXY_POOL") or "").strip()
        if not crudo:
            return []
        if crudo.startswith("["):
            try:
                return [p for p in json.loads(crudo) if isinstance(p, dict) and p.get("server")]
            except ValueError:
                return []
        return [{"server": t.strip() if "://" in t else "http://" + t.strip()} for t in crudo.replace("\n", ",").split(",") if t.strip()]

    def _proxy(self) -> dict | None:
        if not self.proxies:
            return None
        self._turno = (self._turno + 1) % len(self.proxies)
        return self.proxies[self._turno]

    def _semaforo_actual(self) -> asyncio.Semaphore:
        loop = asyncio.get_event_loop()
        if self._semaforo is None or self._loop is not loop:
            self._semaforo = asyncio.Semaphore(self.config.concurrencia)
            self._loop = loop
        return self._semaforo

    async def pedir(self, url: str, *, perfil: Perfil | None = None, referer: str = "", espera_selector: str = "", forzar_cooldown: bool = False) -> Resultado:
        host = _host(url)
        enfriado = self.cooldown.activo(host)
        if enfriado and not forzar_cooldown:
            return Resultado(estado="cooldown", detalle=str(enfriado.get("motivo")))

        hay = disponible()
        if not hay["http"] and not hay["stealth"]:
            return Resultado(estado="sin_motor", detalle="scrapling no instalado")

        elegido = perfil or self.rotador.siguiente(host)
        modo = self.config.modo
        if modo in ("auto", ""):
            modo = "stealth" if self.config.requiere_navegador(host) and hay["stealth"] else "http"
        if modo == "stealth" and not hay["stealth"]:
            modo = "http"

        ultimo = Resultado(estado="sin_intento")
        for intento in range(self.config.reintentos + 1):
            resultado = await self._ejecutar(url, elegido, modo, referer, espera_selector)
            resultado.perfil = elegido.id
            ultimo = resultado
            self.ultimo[host] = resultado
            if resultado.ok:
                self.cooldown.limpiar(host)
                return resultado
            if resultado.bloqueado:
                self.cooldown.registrar(host, resultado.estado)
                return resultado
            if resultado.estado in ESTADOS_REINTENTO and intento < self.config.reintentos:
                espera = self.presupuesto.espera_retry_after(resultado.detalle) if resultado.estado == "rate_limited" else 0
                await asyncio.sleep(espera or min(12.0, 1.3 * (2 ** intento)) + random.uniform(0.2, 0.9))
                elegido = self.rotador.siguiente(host)
        return ultimo

    async def _ejecutar(self, url: str, perfil: Perfil, modo: str, referer: str, espera_selector: str) -> Resultado:
        host = _host(url)
        timeout = self.presupuesto.timeout(host) / 1000.0
        inicio = time.monotonic()
        async with self._semaforo_actual():
            try:
                crudo = await self._llamar(url, perfil, modo, perfil.cabeceras(referer), timeout, espera_selector)
            except asyncio.TimeoutError:
                return Resultado(estado="timeout", motor=modo, ms=_ms(inicio))
            except Exception as exc:
                nombre = type(exc).__name__
                return Resultado(estado="timeout" if "timeout" in nombre.lower() else "network_error", motor=modo, ms=_ms(inicio), detalle=nombre)

        ms = _ms(inicio)
        self.presupuesto.observar(host, ms)
        codigo = int(getattr(crudo, "status", getattr(crudo, "status_code", 0)) or 0)
        texto = _cuerpo(crudo)
        cabeceras = _cabeceras(crudo)
        estado = clasificar(codigo, cabeceras, texto)
        detalle = cabeceras.get("retry-after", "") if estado == "rate_limited" else ""
        return Resultado(ok=estado == "ok", estado=estado, http=codigo, texto=texto, motor=modo, ms=ms, detalle=detalle)

    async def _llamar(self, url: str, perfil: Perfil, modo: str, cabeceras: dict, timeout: float, espera_selector: str) -> Any:
        proxy = self._proxy()
        if modo == "stealth":
            opciones: dict[str, Any] = {
                "headless": True,
                "humanize": True,
                "geoip": True,
                "block_webrtc": True,
                "hide_canvas": True,
                "allow_webgl": True,
                "network_idle": True,
                "real_challenge": True,
                "timeout": int(timeout * 1000),
                "wait": int(self.config.esperas.get("tras_carga_ms") or 1200),
                "extra_headers": cabeceras,
                "useragent": perfil.ua,
                "locale": "es-MX",
                "timezone_id": "America/Mexico_City",
            }
            if espera_selector:
                opciones["wait_selector"] = espera_selector
                opciones["wait_selector_state"] = "attached"
            if self.config.resolver_cloudflare(_host(url)):
                opciones["solve_cloudflare"] = True
            if proxy:
                opciones["proxy"] = proxy
            return await _abrir(_SesionStealth, url, opciones, "fetch")

        opciones = {"stealthy_headers": True, "impersonate": perfil.impersonate, "http3": True, "follow_redirects": True, "headers": cabeceras, "timeout": timeout}
        if proxy:
            opciones["proxy"] = proxy
        return await _abrir(_SesionHttp, url, opciones, "get")

    async def pedir_o_fallback(self, url: str, respaldo: Callable[[], Any] | None = None, *, perfil: Perfil | None = None, referer: str = "", espera_selector: str = "", filas_de: Callable[[Resultado], list[dict]] | None = None) -> Resultado:
        resultado = await self.pedir(url, perfil=perfil, referer=referer, espera_selector=espera_selector)
        if filas_de is not None:
            try:
                resultado.filas = filas_de(resultado)
            except Exception as exc:
                resultado.filas = []
                resultado.detalle = f"extraccion:{type(exc).__name__}"
        if resultado.filas:
            return resultado
        if not self.config.fallback or respaldo is None:
            return resultado
        try:
            heredado = respaldo()
            if asyncio.iscoroutine(heredado):
                heredado = await heredado
        except Exception as exc:
            resultado.detalle = f"fallback_error:{type(exc).__name__}"
            return resultado
        if isinstance(heredado, Resultado):
            heredado.fallback = True
            return heredado
        if hasattr(heredado, "ok") and hasattr(heredado, "estado"):
            return Resultado(ok=bool(getattr(heredado, "ok", False)), estado=str(getattr(heredado, "estado", "fallback")), http=int(getattr(heredado, "http", 0) or 0), texto=str(getattr(heredado, "texto", "") or ""), motor="legacy", fallback=True, detalle="respaldo_pipeline")
        if isinstance(heredado, list):
            resultado.filas = heredado
            resultado.fallback = True
            resultado.detalle = "respaldo_pipeline"
            resultado.ok = resultado.ok or bool(heredado)
            return resultado
        resultado.detalle = "respaldo_sin_datos"
        return resultado

    def pedir_sync(self, url: str, **kw: Any) -> Resultado:
        return _correr(self.pedir(url, **kw))

    def pedir_o_fallback_sync(self, url: str, respaldo: Callable[[], Any] | None = None, **kw: Any) -> Resultado:
        return _correr(self.pedir_o_fallback(url, respaldo, **kw))

    def lote_sync(self, urls: Iterable[str], **kw: Any) -> list[Resultado]:
        return _correr(self.lote(urls, **kw))

    async def lote(self, urls: Iterable[str], **kw: Any) -> list[Resultado]:
        return await asyncio.gather(*(self.pedir(u, **kw) for u in urls))

    def extraer(self, resultado: Resultado, selectores: dict[str, str]) -> list[dict]:
        if not resultado.texto:
            return []
        return extraer_minimo(resultado.texto, selectores)


PRECIO = re.compile(r"\$\s*([\d][\d.,]{1,14})")


def _a_precio(texto: Any) -> float | None:
    try:
        from core.price_sanitizer import parse_price  # type: ignore

        return parse_price(texto)
    except Exception:
        pass
    if texto is None:
        return None
    try:
        valor = float(re.sub(r"[^\d.]", "", str(texto)))
    except ValueError:
        return None
    return valor if 0 < valor < 2_000_000 else None


def _texto_plano(trozo: str) -> str:
    import html as _html

    limpio = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", trozo, flags=re.S | re.I)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", limpio))).strip()


def extraer_minimo(cuerpo: str, selectores: dict[str, str]) -> list[dict]:
    """Solo nodos de tarjeta. Usa Selector de Scrapling; si no, el extractor de perimetro."""
    regla = selectores.get("card") or ""
    if not regla:
        return []
    if _Selector is not None:
        try:
            arbol = _Selector(cuerpo)
            filas = []
            for nodo in arbol.css(regla):
                enlace = ""
                for ancla in nodo.css(selectores.get("link") or "a[href]"):
                    enlace = ancla.attrib.get("href") or ""
                    if enlace:
                        break
                titulo = ""
                for titulo_nodo in nodo.css(selectores.get("title") or "a"):
                    titulo = (titulo_nodo.text or "").strip() or titulo_nodo.attrib.get("title", "")
                    if titulo:
                        break
                precio = None
                if selectores.get("price"):
                    for nodo_precio in nodo.css(selectores["price"]):
                        precio = _a_precio((nodo_precio.text or "").strip())
                        if precio:
                            break
                if precio is None:
                    limpios = [p for p in (_a_precio(m) for m in PRECIO.findall(_texto_plano(str(nodo.html_content or "")))) if p]
                    precio = min(limpios) if limpios else None
                if titulo and enlace and precio:
                    filas.append({"titulo": titulo, "url": enlace, "precio_actual": precio})
            return filas
        except Exception:
            pass
    try:
        from perimetro.extraccion import selectores as _sel  # type: ignore

        return _sel(cuerpo, "", "", selectores)
    except Exception:
        return []


def _cuerpo(respuesta: Any) -> str:
    for atributo in ("body", "html_content", "text"):
        valor = getattr(respuesta, atributo, None)
        if isinstance(valor, bytes):
            return valor.decode("utf-8", "replace")
        if isinstance(valor, str):
            return valor
    return str(respuesta or "")


def _cabeceras(respuesta: Any) -> dict[str, str]:
    try:
        return {str(k).lower(): str(v) for k, v in dict(getattr(respuesta, "headers", {}) or {}).items()}
    except Exception:
        return {}


def _ms(inicio: float) -> int:
    return int((time.monotonic() - inicio) * 1000)


async def _abrir(clase: Any, url: str, opciones: dict, metodo: str) -> Any:
    if clase is None:
        raise RuntimeError("motor no disponible")
    sesion = clase(**opciones)
    if hasattr(sesion, "__aenter__"):
        async with sesion as abierta:
            llamada = getattr(abierta, metodo, None) or getattr(abierta, "fetch", None) or getattr(abierta, "get", None)
            resultado = llamada(url)
            if asyncio.iscoroutine(resultado):
                resultado = await resultado
        return resultado
    with sesion as abierta:
        llamada = getattr(abierta, metodo, None) or getattr(abierta, "fetch", None) or getattr(abierta, "get", None)
        return llamada(url)


def _correr(coro: Awaitable[Any]) -> Any:
    try:
        return asyncio.run(coro)  # type: ignore[arg-type]
    except RuntimeError:
        bucle = asyncio.new_event_loop()
        try:
            return bucle.run_until_complete(coro)  # type: ignore[arg-type]
        finally:
            bucle.close()


_MOTOR: Motor | None = None
_LOCK = threading.Lock()


def motor() -> Motor:
    global _MOTOR
    with _LOCK:
        if _MOTOR is None:
            _MOTOR = Motor()
        return _MOTOR


def reiniciar() -> None:
    global _MOTOR
    with _LOCK:
        _MOTOR = None
