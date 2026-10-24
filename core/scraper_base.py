"""Contrato común para scrapers de DexterH4ck-ofertas.

La capa base NO intenta evadir CAPTCHA/WAF ni falsificar identidad de aplicaciones.
Centraliza timeouts, reintentos conservadores, User-Agent honesto y normalización
de resultados para que los adaptadores puedan usar APIs/documentos públicos.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import logging
import os
import threading
import time

import requests

DEFAULT_HEADERS = {
    "User-Agent": "DexterH4ck-ofertas/2.0 (+deal-monitor; es-MX)",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
    "Accept": "application/json,text/plain,*/*;q=0.8",
}


@dataclass
class ScraperContext:
    timeout: float = 10.0
    max_retries: int = 2
    max_workers: int = 8
    headers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADERS))



logger = logging.getLogger("DexterH4ck.ScraperApiManager")


class ScraperApiManager:
    """Singleton con límite de créditos de proxy por ciclo de proceso."""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        with self._instance_lock:
            if self._initialized:
                return
            self._state_lock = threading.Lock()
            self._credits_used = 0
            self._credit_limit = self._read_limit()
            self._warned_limit = False
            self._initialized = True

    @staticmethod
    def _read_limit() -> int:
        try:
            return max(0, int(os.environ.get("MAX_PROXY_CREDITS_PER_CYCLE", "100")))
        except (TypeError, ValueError):
            logger.warning("Límite de créditos inválido; se usa 100.")
            return 100

    def reset_cycle(self) -> None:
        with self._state_lock:
            self._credits_used = 0
            self._credit_limit = self._read_limit()
            self._warned_limit = False

    def _reserve_credit(self) -> bool:
        with self._state_lock:
            if self._credits_used >= self._credit_limit:
                if not self._warned_limit:
                    logger.warning("Cuota de proxy agotada (%d/%d); se usará HTTP directo.", self._credits_used, self._credit_limit)
                    self._warned_limit = True
                return False
            self._credits_used += 1
            return True

    @staticmethod
    def _target_url(url: str, params: Any) -> str:
        if not params:
            return url
        parts = urlsplit(url)
        current = parse_qsl(parts.query, keep_blank_values=True)
        extra = list(params.items()) if isinstance(params, dict) else list(params)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(current + extra, doseq=True), parts.fragment))

    def request(self, session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
        enabled = os.environ.get("SCRAPER_API_ENABLED", "true").strip().lower() in {"1", "true", "yes", "si"}
        api_key = os.environ.get("SCRAPER_API_KEY", "").strip()
        endpoint = os.environ.get("SCRAPER_API_ENDPOINT", "https://api.scraperapi.com").strip()
        timeout = kwargs.pop("timeout", 10.0)
        if not enabled or not api_key or not endpoint:
            return session.get(url, timeout=timeout, **kwargs)
        if not self._reserve_credit():
            return session.get(url, timeout=timeout, **kwargs)
        target = self._target_url(url, kwargs.pop("params", None))
        return session.get(endpoint, params={"api_key": api_key, "url": target}, timeout=timeout, **kwargs)


class BaseScraper(ABC):
    """Interfaz única para todos los adaptadores de tienda."""

    store: str = "unknown"

    def __init__(self, context: ScraperContext | None = None) -> None:
        self.context = context or ScraperContext()
        self.session = requests.Session()
        self.session.headers.update(self.context.headers)

    @abstractmethod
    def discover(self) -> list[dict[str, Any]]:
        """Devuelve candidatos normalizados; nunca publica directamente."""
        raise NotImplementedError

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        base_timeout = float(kwargs.pop("timeout", self.context.timeout))
        last_error: Exception | None = None
        transient = {408, 425, 429, 500, 502, 503, 504, 522, 524}

        for attempt in range(self.context.max_retries + 1):
            # Aumenta moderadamente el timeout en cada intento para evitar que
            # conexiones lentas de catálogos públicos se queden colgadas.
            timeout = min(base_timeout * (1.35**attempt), 45.0)
            try:
                response = ScraperApiManager().request(self.session, url, timeout=timeout, **kwargs)

                # 401/403/412/451 no se "combaten" con más tráfico: normalmente
                # significan política de acceso o contenido condicionado.
                if response.status_code not in transient:
                    return response

                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    delay = min(float(retry_after), 20.0)
                else:
                    delay = min(1.0 * (2**attempt), 12.0)

                # Jitter evita que varias tiendas vuelvan a golpear al mismo
                # tiempo al recuperarse de un 429/503.
                delay += __import__("random").uniform(0.20, 0.90)

                if attempt < self.context.max_retries:
                    time.sleep(delay)
                    continue
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.context.max_retries:
                    delay = min(1.0 * (2**attempt), 12.0)
                    delay += __import__("random").uniform(0.20, 0.90)
                    time.sleep(delay)

        raise last_error or RuntimeError(f"{self.store}: request failed")

    def normalize(
        self, *, title: str, url: str, price: Any, previous_price: Any = None, **extra: Any
    ) -> dict[str, Any]:
        return {
            "tienda": self.store,
            "titulo": str(title or "").strip(),
            "url": str(url or "").strip(),
            "precio_actual": price,
            "precio_anterior": previous_price,
            "origen_link": "api_publica",
            **extra,
        }


def run_scrapers_parallel(scrapers: Iterable[BaseScraper], max_workers: int | None = None) -> list[dict[str, Any]]:
    """Ejecuta cada tienda en paralelo y aísla el fallo de una tienda."""
    scrapers = list(scrapers)
    if not scrapers:
        return []
    workers = max_workers or min(len(scrapers), 8)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="store") as pool:
        futures = {pool.submit(scraper.discover): scraper for scraper in scrapers}
        for future in as_completed(futures):
            scraper = futures[future]
            try:
                results.extend(future.result() or [])
            except Exception as exc:
                print(f"[SCRAPER:{scraper.store}] ERROR aislado: {type(exc).__name__}: {exc}")
    return results
