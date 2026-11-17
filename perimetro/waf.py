# perimetro/waf.py
# Distingue OK de challenge/bloqueo real: un 200 con interstitial NO es éxito.
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_COOLDOWN = Path(os.environ.get("PERIMETRO_COOLDOWN", "") or RAIZ / "perimetro_cooldown.json")
COOLDOWN_MIN = 45

try:
    from core.source_resilience import BLOCKING_STATES, classify_status  # type: ignore
except ImportError:  # pragma: no cover
    BLOCKING_STATES = {401, 403, 407, 429, 451, 500, 502, 503, 504, 520, 521, 522, 523, 524}

    def classify_status(codigo: int) -> str:  # type: ignore
        if codigo == 200:
            return "ok"
        if codigo in (401, 407):
            return "unauthorized"
        if codigo == 403:
            return "forbidden"
        if codigo == 429:
            return "rate_limited"
        if codigo == 404:
            return "not_found"
        if codigo >= 500:
            return "server_error"
        if 300 <= codigo < 400:
            return "redirect"
        return f"http_{codigo}"

BLOQUEOS = {"forbidden", "rate_limited", "blocked_waf", "captcha_required", "unauthorized", "server_error"}

FIRMAS = (
    ("captcha_required", ("g-recaptcha", "h-captcha", "hcaptcha", "cf-turnstile", "funcaptcha", "px-captcha", "captcha-delivery")),
    ("blocked_waf", ("just a moment", "checking your browser", "enable javascript and cookies", "attention required", "access denied", "request unsuccessful. incapsula", "datadome", "_incapsula_resource", "px-captcha", "sec-if-cpt-container")),
    ("rate_limited", ("too many requests", "rate limit", "you have exceeded", "slow down")),
)

CABECERAS_WAF = {
    "blocked_waf": ("cf-mitigated", "x-datadome", "x-akamai-bm", "x-kpsdk-ct", "x-iinfo"),
    "captcha_required": ("x-datadome-captcha", "x-px-captcha"),
}

MARCAS_WAF = {
    "cloudflare": ("cf-ray", "cf-cache-status", "__cf_bm", "cloudflare"),
    "akamai": ("_abck", "akamai", "x-akamai"),
    "datadome": ("datadome", "x-datadome"),
    "perimeterx": ("_px", "px-captcha", "perimeterx"),
    "incapsula": ("visid_incap", "incap_ses", "_incapsula_resource"),
    "kasada": ("x-kpsdk", "kasada"),
    "imperva": ("x-iinfo", "imperva"),
}


def _muestra(cuerpo: str) -> str:
    return (cuerpo or "")[:40000].lower()


def clasificar(codigo: int, cabeceras: dict[str, str] | None = None, cuerpo: str = "") -> str:
    base = classify_status(codigo)
    if base == "ok":
        muestra = _muestra(cuerpo)
        bajo = {str(k).lower(): str(v) for k, v in (cabeceras or {}).items()}
        for estado, firmas in FIRMAS:
            if any(firma in muestra for firma in firmas):
                return estado
        for estado, claves in CABECERAS_WAF.items():
            if any(clave in bajo for clave in claves):
                if estado == "blocked_waf" and bajo.get("x-datadome") and "geo.captcha-delivery" in muestra:
                    return "captcha_required"
                if estado == "blocked_waf":
                    return estado
        if len(cuerpo or "") < 1200 and re.search(r"challenge|verific|robot", muestra):
            return "blocked_waf"
        return "ok"
    if base in ("forbidden", "unauthorized") and cuerpo:
        muestra = _muestra(cuerpo)
        if any(f in muestra for f in ("captcha", "turnstile", "just a moment")):
            return "captcha_required"
        return "blocked_waf"
    return base


def waf_de(cabeceras: dict[str, str] | None, cuerpo: str = "") -> str:
    bajo = {str(k).lower(): str(v) for k, v in (cabeceras or {}).items()}
    muestra = _muestra(cuerpo)
    for proveedor, pistas in MARCAS_WAF.items():
        if any(p in bajo or p in muestra for p in pistas):
            return proveedor
    return ""


class Cooldown:
    def __init__(self, ruta: Path | None = None, minutos: int | None = None) -> None:
        self.ruta = Path(ruta or RUTA_COOLDOWN)
        self.minutos = minutos if minutos is not None else _minutos()

    def _leer(self) -> dict[str, dict]:
        try:
            data = json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _escribir(self, data: dict[str, dict]) -> None:
        try:
            self.ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def activo(self, host: str) -> dict | None:
        entrada = self._leer().get(host)
        if not entrada:
            return None
        if float(entrada.get("hasta", 0)) <= time.time():
            return None
        return entrada

    def registrar(self, host: str, motivo: str, waf: str = "") -> None:
        if not host:
            return
        data = self._leer()
        data[host] = {"motivo": motivo, "waf": waf, "hasta": time.time() + self.minutos * 60, "iso": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._escribir(data)

    def limpiar(self, host: str) -> None:
        data = self._leer()
        if data.pop(host, None) is not None:
            self._escribir(data)

    def listar(self) -> dict[str, dict]:
        ahora = time.time()
        return {h: v for h, v in self._leer().items() if float(v.get("hasta", 0)) > ahora}


def _minutos() -> int:
    try:
        return max(1, int(os.environ.get("PERIMETRO_COOLDOWN_MIN", "") or COOLDOWN_MIN))
    except (TypeError, ValueError):
        return COOLDOWN_MIN
