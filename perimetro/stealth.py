# perimetro/stealth.py
from __future__ import annotations

import os
import random

NAVEGADORES = ("chrome", "chrome131", "chrome124", "edge101", "safari17_0")
ACEPTAR = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"


def _si(nombre: str, defecto: bool = False) -> bool:
    crudo = os.environ.get(nombre)
    if crudo is None:
        return defecto
    return crudo.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def impersonate() -> str:
    return (os.environ.get("PERIMETRO_IMPERSONATE", "") or random.choice(NAVEGADORES)).strip()


def headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": ACEPTAR,
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        base.update({str(k): str(v) for k, v in extra.items() if v})
    return base


def kwargs_http() -> dict:
    return {"stealthy_headers": True, "impersonate": impersonate(), "http3": _si("PERIMETRO_HTTP3", True), "follow_redirects": True, "max_redirects": 8, "headers": headers()}


def kwargs_stealth(ms: int = 45000, espera_selector: str = "") -> dict:
    opciones = {
        "headless": _si("PERIMETRO_HEADLESS", True),
        "humanize": _si("PERIMETRO_HUMANIZE", True),
        "geoip": _si("PERIMETRO_GEOIP", True),
        "block_webrtc": True,
        "allow_webgl": _si("PERIMETRO_WEBGL", True),
        "hide_canvas": _si("PERIMETRO_HIDE_CANVAS", True),
        "network_idle": _si("PERIMETRO_NETWORK_IDLE", True),
        "timeout": ms,
        "wait": int(os.environ.get("PERIMETRO_WAIT_MS", "1200") or 1200),
        "real_challenge": _si("PERIMETRO_REAL_CHALLENGE", True),
        "solve_cloudflare": _si("PERIMETRO_SOLVE_CLOUDFLARE", False),
        "extra_headers": headers(),
        "disable_resources": _si("PERIMETRO_BLOQUEAR_RECURSOS", False),
    }
    if espera_selector:
        opciones["wait_selector"] = espera_selector
        opciones["wait_selector_state"] = "attached"
    return opciones


SCRIPT_EVASION = """
(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['es-MX', 'es', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  window.chrome = window.chrome || { runtime: {} };
  if (!window.Notification) { window.Notification = { permission: 'default' }; }
})();
"""


def humano() -> float:
    return random.uniform(0.6, 2.2)
