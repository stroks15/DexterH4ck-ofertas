"""Gestor centralizado de respaldo (fallback) con ScraperAPI.

Principios:
- ScraperAPI es SOLO un respaldo: se consume únicamente si la petición normal (directa o
  vía Apps Script) falla con 403/412/503, timeout o error de conexión.
- Si no hay ``SCRAPERAPI_KEY``, o la cuota/licencia se agota, el gestor se desactiva solo y el
  monitor sigue con el flujo normal (sin proxy de pago). Nunca lanza excepciones propias.
- Nunca se envían por ScraperAPI URLs con credenciales (tokens en query, Authorization,
  cookies) ni hosts propios/sensibles (Apps Script, Telegram, Supabase, Gemini).
- No resuelve CAPTCHA por su cuenta: si la respuesta del proxy sigue siendo un reto, se
  considera fallida y no se escala más (salvo ``render=True`` explícito, una sola vez).
- La API key jamás se registra en logs ni queda en ``response.url``.

Variables de entorno:
    SCRAPERAPI_KEY           clave (sin ella el gestor queda apagado)
    SCRAPERAPI_ENABLED       true/false (por defecto true si hay clave)
    SCRAPERAPI_MAX_CREDITS   presupuesto estimado por ejecución (por defecto 100)
    SCRAPERAPI_TIMEOUT       segundos por petición al proxy (por defecto 60)
    SCRAPERAPI_COUNTRY       country_code de geolocalización (por defecto mx)
    SCRAPERAPI_RENDER_STORES lista separada por comas de tiendas que pueden escalar a render
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Mapping
from urllib.parse import parse_qsl, urlparse

import requests

logger = logging.getLogger("DexterH4ck.ScraperApi")

API_ENDPOINT = "https://api.scraperapi.com/"

# Códigos del destino que activan el respaldo (además de Timeout / ConnectionError).
FALLBACK_STATUSES = frozenset({403, 412, 503})

# Respuestas de ScraperAPI que indican cuenta sin crédito o clave inválida.
# Documentación oficial: 403 = créditos agotados, 401 = clave inválida.
QUOTA_STATUSES = frozenset({402, 433})
_QUOTA_HINTS = ("credit", "exhaust", "quota", "plan", "limit")

CREDITS_STANDARD = 1
CREDITS_RENDER = 10  # estimación conservadora; el costo real depende del dominio

NEVER_PROXY_HOSTS = (
    "script.google.com",
    "script.googleusercontent.com",
    "api.telegram.org",
    "supabase.co",
    "generativelanguage.googleapis.com",
    "api.scraperapi.com",
)
SECRET_PARAMS = frozenset(
    {"key", "api_key", "apikey", "token", "access_token", "auth", "password", "secret", "sig", "signature"}
)
SECRET_HEADERS = frozenset({"authorization", "cookie", "x-api-key", "apikey", "proxy-authorization"})


def _truthy(raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _env_number(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


class ScraperApiManager:
    """Respaldo ScraperAPI con presupuesto, circuito por host y autodesactivación."""

    def __init__(
        self,
        api_key: str | None = None,
        enabled: bool | None = None,
        max_credits: float | None = None,
        timeout: float | None = None,
        country: str | None = None,
    ) -> None:
        self._key = (os.getenv("SCRAPERAPI_KEY", "") if api_key is None else api_key).strip()
        wanted = _truthy(os.getenv("SCRAPERAPI_ENABLED"), True) if enabled is None else enabled
        self._enabled = bool(self._key) and wanted
        self.max_credits = (
            _env_number("SCRAPERAPI_MAX_CREDITS", 100, 0, 5000) if max_credits is None else float(max_credits)
        )
        self.timeout = _env_number("SCRAPERAPI_TIMEOUT", 60, 10, 90) if timeout is None else float(timeout)
        self.country = (os.getenv("SCRAPERAPI_COUNTRY", "mx") if country is None else country).strip().lower()
        self.render_stores = {
            s.strip().lower() for s in os.getenv("SCRAPERAPI_RENDER_STORES", "").split(",") if s.strip()
        }
        if self._enabled:
            self.disabled_reason = ""
        else:
            self.disabled_reason = "sin SCRAPERAPI_KEY" if not self._key else "apagado por entorno"
        self.credits_used = 0.0
        self.fallbacks_ok = 0
        self.fallbacks_failed = 0
        self._host_failures: dict[str, int] = {}
        self._budget_warned = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ estado
    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._key)

    def render_for(self, store: str) -> bool:
        """True si la tienda puede escalar a render JS (SCRAPERAPI_RENDER_STORES)."""
        return str(store or "").strip().lower() in self.render_stores

    def summary(self) -> dict:
        """Resumen sin secretos para el reporte del monitor."""
        return {
            "enabled": self.enabled,
            "credits_used_estimated": self.credits_used,
            "fallbacks_ok": self.fallbacks_ok,
            "fallbacks_failed": self.fallbacks_failed,
            "disabled_reason": self.disabled_reason,
        }

    def _disable(self, reason: str, *, warning: str) -> None:
        with self._lock:
            was_enabled = self._enabled
            self._enabled = False
            self.disabled_reason = reason
        if was_enabled:
            logger.warning(warning)
            os.environ["SCRAPERAPI_ENABLED"] = "false"

    # ------------------------------------------------------- decisiones
    @staticmethod
    def should_fallback_status(status_code: int) -> bool:
        return status_code in FALLBACK_STATUSES

    @staticmethod
    def should_fallback_exception(exc: BaseException) -> bool:
        return isinstance(exc, (requests.Timeout, requests.ConnectionError))

    def can_proxy(self, url: str, headers: Mapping[str, str] | None = None) -> bool:
        """False si la URL/cabeceras llevarían credenciales o hosts propios a un tercero."""
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or not host:
            return False
        if any(host == h or host.endswith("." + h) for h in NEVER_PROXY_HOSTS):
            return False
        if parsed.username or parsed.password:
            return False
        if any(name.lower() in SECRET_PARAMS for name, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            return False
        if headers and any(str(name).lower() in SECRET_HEADERS for name in headers):
            return False
        return True

    @staticmethod
    def _looks_like_quota_error(response: requests.Response) -> bool:
        if response.status_code in QUOTA_STATUSES:
            return True
        if response.status_code == 403:
            body = (response.text or "")[:400].lower()
            return any(hint in body for hint in _QUOTA_HINTS)
        return False

    @staticmethod
    def _usable(response: requests.Response) -> bool:
        """Respuesta aprovechable: éxito, o 404/410 reales del destino; y sin reto visible."""
        if response.status_code >= 400 and response.status_code not in (404, 410):
            return False
        if response.status_code >= 400:
            return True
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            return True
        try:
            from core.source_resilience import BLOCK_SIGNATURES
        except ImportError:  # pragma: no cover
            return True
        body = (response.text or "")[:20000].lower()
        return not any(sig in body for sig in BLOCK_SIGNATURES)

    # ------------------------------------------------------------ fetch
    def fetch(
        self, url: str, *, render: bool = False, headers: Mapping[str, str] | None = None
    ) -> requests.Response | None:
        """Una petición vía ScraperAPI. Devuelve Response o None; nunca lanza."""
        if not self.enabled or not self.can_proxy(url, headers):
            return None
        host = (urlparse(url).hostname or "").lower()
        cost = CREDITS_RENDER if render else CREDITS_STANDARD
        with self._lock:
            if self._host_failures.get(host, 0) >= 2:
                return None
            if self.credits_used + cost > self.max_credits:
                if not self._budget_warned:
                    self._budget_warned = True
                    logger.warning("ScraperAPI: presupuesto por ejecución agotado (%s créditos).", self.max_credits)
                return None
            self.credits_used += cost

        params = {"api_key": self._key, "url": url}
        if self.country:
            params["country_code"] = self.country
        if render:
            params["render"] = "true"

        try:
            response = requests.get(API_ENDPOINT, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            # El mensaje de requests incluiría la URL con la api_key: se registra solo el tipo.
            self._record_failure(host, refund=cost)
            logger.warning("ScraperAPI: error de red (%s) para %s.", type(exc).__name__, host)
            return None

        if response.status_code == 401:
            self._disable("clave inválida", warning="WARNING: ScraperAPI key invalid; fallback disabled")
            self._record_failure(host, refund=cost)
            return None
        if self._looks_like_quota_error(response):
            self._disable(
                "cuota agotada o vencida", warning="WARNING: ScraperAPI quota exhausted or expired; fallback disabled"
            )
            self._record_failure(host, refund=cost)
            return None
        if response.status_code >= 500 or response.status_code in (403, 429):
            # 5xx no se cobra; 429 = límite de concurrencia; 403 sin pista de cuota = bloqueo.
            self._record_failure(host, refund=cost if response.status_code >= 500 else 0)
            return None

        with self._lock:
            self.fallbacks_ok += 1
        # La respuesta no debe exponer la api_key a código que registre response.url.
        response.url = url
        try:
            response.request.url = url
        except AttributeError:  # pragma: no cover
            pass
        response.headers["X-DexterH4ck-Via"] = "scraperapi-render" if render else "scraperapi"
        return response

    def _record_failure(self, host: str, *, refund: float = 0) -> None:
        with self._lock:
            self.fallbacks_failed += 1
            self._host_failures[host] = self._host_failures.get(host, 0) + 1
            self.credits_used = max(0.0, self.credits_used - refund)

    # -------------------------------------------------------------- run
    def run(
        self,
        primary: Callable[[], requests.Response],
        url: str,
        *,
        render: bool = False,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        """Ejecuta ``primary``; si falla por bloqueo/timeout, reintenta vía ScraperAPI.

        ``render=True`` permite UN escalón extra con renderizado JS si el proxy normal falla.
        Si el respaldo también falla se devuelve/relanza el resultado original.
        """
        try:
            response = primary()
        except requests.RequestException as exc:
            if self.enabled and self.should_fallback_exception(exc):
                alt = self._fallback(url, render, headers)
                if alt is not None:
                    return alt
            raise
        if self.enabled and self.should_fallback_status(response.status_code):
            alt = self._fallback(url, render, headers)
            if alt is not None:
                return alt
        return response

    def _fallback(
        self, url: str, render: bool, headers: Mapping[str, str] | None
    ) -> requests.Response | None:
        alt = self.fetch(url, headers=headers)
        if alt is not None and self._usable(alt):
            return alt
        if render:
            alt = self.fetch(url, render=True, headers=headers)
            if alt is not None and self._usable(alt):
                return alt
        return None


_manager: ScraperApiManager | None = None
_manager_lock = threading.Lock()


def get_scraperapi_manager() -> ScraperApiManager:
    """Instancia única por proceso (los workers paralelos comparten presupuesto y estado)."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ScraperApiManager()
        return _manager


def reset_scraperapi_manager() -> None:
    """Solo para pruebas."""
    global _manager
    with _manager_lock:
        _manager = None
