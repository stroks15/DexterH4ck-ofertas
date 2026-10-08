"""Utilidades de resiliencia para fuentes comerciales.

No intenta saltar WAF/CAPTCHA. Clasifica respuestas bloqueadas, aplica backoff
conservador y permite que el monitor cambie a una fuente alternativa.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Any

TRANSIENT = {408, 425, 429, 500, 502, 503, 504, 522, 524}
STOP_THIS_CYCLE = {401, 403, 404, 407, 451}


class SourceCircuit:
    def __init__(self, name: str):
        self.name = name
        self.failures = defaultdict(int)
        self.paused = False
        self.reason = ""

    def record(self, status: int | None, reason: str = "") -> bool:
        if status is None:
            return False
        self.failures[status] += 1
        if status in STOP_THIS_CYCLE:
            self.paused = True
            self.reason = reason or f"HTTP {status}"
        elif status in TRANSIENT and self.failures[status] >= 2:
            self.paused = True
            self.reason = reason or f"HTTP {status} repetido"
        return self.paused

    def can_continue(self) -> bool:
        return not self.paused

    def backoff(self, status: int | None, attempt: int = 0, retry_after: Any = None) -> None:
        if status not in TRANSIENT:
            return
        try:
            server = float(retry_after)
        except (TypeError, ValueError):
            server = 0.0
        delay = server if server > 0 else min(12.0, 2.0 ** attempt)
        delay += random.uniform(0.15, 0.65)
        time.sleep(delay)


def status_summary(circuit: SourceCircuit) -> dict[str, Any]:
    return {
        "paused": circuit.paused,
        "reason": circuit.reason,
        "failures": dict(circuit.failures),
    }


# ---------------------------------------------------------------------------
# Clasificación de estados HTTP y sondeo de fuentes (preflight/diagnóstico).
# No intenta evadir WAF/CAPTCHA: solo distingue y reporta cada situación.
# ---------------------------------------------------------------------------
import requests  # noqa: E402

HONEST_USER_AGENT = "DexterH4ck-ofertas/2.0 (+deal-monitor; es-MX)"
PROBE_HEADERS = {
    "User-Agent": HONEST_USER_AGENT,
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
}

# Firmas de bloqueo/reto en el cuerpo de la respuesta (se conservan del preflight original).
BLOCK_SIGNATURES = ("access denied", "temporarily blocked", "captcha")

# Estados que hacen que ESA fuente se omita en el ciclo (nunca detienen las demás).
BLOCKING_STATES = {"blocked", "forbidden", "rate_limited", "server_error", "unavailable"}

# Dominios/URLs públicas de salud por tienda (mismas 10 tiendas objetivo del monitor).
STORE_TARGETS = {
    "Walmart MX": ["https://www.walmart.com.mx/"],
    "Bodega Aurrera": ["https://www.bodegaaurrera.com.mx/"],
    "Chedraui": ["https://www.chedraui.com.mx/"],
    "Soriana": ["https://www.soriana.com/"],
    "Liverpool": ["https://www.liverpool.com.mx/"],
    "Amazon MX": ["https://www.amazon.com.mx/"],
    "Mercado Libre MX": ["https://api.mercadolibre.com/sites/MLM", "https://www.mercadolibre.com.mx/"],
    "Coppel": ["https://www.coppel.com/"],
    "Suburbia": ["https://www.suburbia.com.mx/"],
    "Oferstock": ["https://www.oferstock.com.mx/"],
}


def classify_status(status: int | None) -> str:
    """Convierte un código HTTP en un estado distinto por situación (no todo es 'error')."""
    if status is None:
        return "network_error"
    if 200 <= status < 300:
        return "ok"
    if 300 <= status < 400:
        return "redirect"
    exact = {
        400: "bad_request",        # solicitud/endpoint mal formado
        401: "unauthorized",       # credencial inválida o ausente
        403: "forbidden",          # acceso denegado/bloqueado
        404: "not_found",          # endpoint o producto inexistente
        408: "timeout",
        409: "conflict",
        429: "rate_limited",
        500: "server_error",
        502: "unavailable",
        503: "unavailable",
        504: "unavailable",
    }
    if status in exact:
        return exact[status]
    if status >= 500:
        return "server_error"
    return "http_error"


def probe_url(url: str, session: "requests.Session | None" = None, timeout: float = 15.0) -> dict[str, Any]:
    """Sondea una URL pública y devuelve estado clasificado. Nunca lanza excepciones."""
    own = session is None
    session = session or requests.Session()
    try:
        response = session.get(url, headers=PROBE_HEADERS, timeout=timeout, allow_redirects=True)
        status = response.status_code
        state = classify_status(status)
        try:
            body = (response.text or "")[:200_000].lower()
        except Exception:  # cuerpo ilegible: no es un bloqueo demostrable
            body = ""
        if any(sig in body for sig in BLOCK_SIGNATURES):
            state = "blocked"
        return {"url": url, "status": status, "state": state, "final_url": str(getattr(response, "url", url))}
    except requests.Timeout as exc:
        return {"url": url, "status": None, "state": "timeout", "error": type(exc).__name__}
    except requests.RequestException as exc:
        return {"url": url, "status": None, "state": "network_error", "error": type(exc).__name__}
    except Exception as exc:  # defensivo: el sondeo jamás debe tumbar el preflight
        return {"url": url, "status": None, "state": "network_error", "error": type(exc).__name__}
    finally:
        if own:
            session.close()


def summarize_store(probes: list[dict[str, Any]]) -> dict[str, Any]:
    """Estado de una tienda: ok si alguna sonda responde ok; si no, el estado de la primera."""
    if any(p.get("state") == "ok" for p in probes):
        state = "ok"
    else:
        state = probes[0].get("state", "network_error") if probes else "network_error"
    return {"state": state, "blocked": state in BLOCKING_STATES, "probes": probes}
