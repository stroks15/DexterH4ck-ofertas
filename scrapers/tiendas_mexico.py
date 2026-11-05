"""Legacy HTML discovery implemented as a conservative Scrapling Spider.

Only public product/search pages are requested. This module deliberately does not
impersonate mobile apps, solve challenges, rotate proxies, or retry perimeter blocks.
A 403/412/503 ends that store's work for the cycle and is persisted to a short
cooldown record so ephemeral CI runs do not immediately hammer the same host.
"""
from __future__ import annotations

import hashlib
from email.utils import parsedate_to_datetime
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from core.price_sanitizer import parse_price, sanear_par_precios

logger = logging.getLogger("DexterH4ck.TiendasMexico")
LAST_STATUS: dict[str, dict] = {}
TERMINOS = ["liquidacion"]
COOLDOWN_FILE = Path(os.getenv("FUENTES_COOLDOWN_FILE", "fuentes_cooldown.json"))

URLS_BUSQUEDA = {
    "amazon": "https://www.amazon.com.mx/s?k={q}",
    "soriana": "https://www.soriana.com/buscar?q={q}",
    "liverpool": "https://www.liverpool.com.mx/tienda?s={q}",
}

# Optional, operator-supplied public search URLs. Never store credentials here.
def _configured_urls() -> dict[str, str]:
    urls = dict(URLS_BUSQUEDA)
    raw = os.getenv("LEGACY_SEARCH_URLS_JSON", "").strip()
    if raw:
        try:
            supplied = json.loads(raw)
            if isinstance(supplied, dict):
                for name, template in supplied.items():
                    parsed = urlparse(str(template))
                    if parsed.scheme == "https" and parsed.netloc and "{q}" in str(template):
                        urls[str(name).lower()] = str(template)
        except (ValueError, TypeError):
            logger.warning("LEGACY_SEARCH_URLS_JSON no contiene un objeto JSON válido.")
    return urls


def _hash_id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:16]


def _load_cooldowns() -> dict:
    try:
        data = json.loads(COOLDOWN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clear_cooldown(store: str) -> None:
    data = _load_cooldowns()
    if store not in data:
        return
    data.pop(store, None)
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = COOLDOWN_FILE.with_suffix(COOLDOWN_FILE.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(COOLDOWN_FILE)


def _save_cooldown(store: str, reason: str, seconds: int) -> None:
    data = _load_cooldowns()
    now = datetime.now(timezone.utc)
    data[store] = {
        "reason": reason,
        "updated_at": now.isoformat(),
        "blocked_until": (now + timedelta(seconds=max(60, seconds))).isoformat(),
    }
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = COOLDOWN_FILE.with_suffix(COOLDOWN_FILE.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(COOLDOWN_FILE)


def _cooldown_active(store: str) -> bool:
    row = _load_cooldowns().get(store)
    if not isinstance(row, dict):
        return False
    try:
        until = datetime.fromisoformat(str(row.get("blocked_until")))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return until > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _retry_after_seconds(headers) -> int | None:
    headers = headers or {}
    value = str(headers.get("retry-after") or headers.get("Retry-After") or "").strip()
    if not value:
        return None
    try:
        return max(1, int(float(value)))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(1, int((when - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None


def _status_reason(status: int) -> tuple[str, int]:
    if status == 403:
        return "HTTP_403_WAF_BLOCK", 6 * 60 * 60
    if status == 412:
        return "HTTP_412_PRECONDITION_BLOCK", 6 * 60 * 60
    if status == 503:
        return "HTTP_503_SERVICE_UNAVAILABLE", 30 * 60
    if status == 429:
        return "HTTP_429_RATE_LIMIT", 60 * 60
    return f"HTTP_{status}", 30 * 60


def _parse_price_node(node) -> float | None:
    if node is None:
        return None
    return parse_price(node.get("content") or node.get("data-price") or node.get_text(" ", strip=True))


def _extract_cards(html_source: str, store: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html_source or "", "html.parser")
    results: list[dict] = []
    if store == "amazon":
        cards = soup.select("div[data-component-type='s-search-result']")
        for card in cards:
            title_el, link_el = card.select_one("h2 a span"), card.select_one("h2 a")
            price_el = card.select_one(".a-price .a-offscreen") or card.select_one(".a-price-whole")
            if not (title_el and link_el and price_el):
                continue
            title = title_el.get_text(" ", strip=True)
            current = _parse_price_node(price_el)
            url = urljoin("https://www.amazon.com.mx", link_el.get("href", ""))
            if current and title and es_url_producto(url):
                results.append({"id": card.get("data-asin") or _hash_id(url), "name": title,
                    "titulo": title, "brand": "", "url": url, "precio_actual": current,
                    "precio_anterior": None, "tienda": "Amazon MX", "tipo_fuente": "ONLINE"})
        return results

    if store == "soriana":
        cards = soup.select(".product-card")
    elif store == "liverpool":
        cards = soup.select("[data-testid*='product'], .product, .product-tile")
    else:
        cards = []

    for card in cards:
        link = card.select_one("a.product-title-link, a[href*='/pdp/'], a[href*='/producto/'], a[href*='/p/']")
        title_el = card.select_one("h2, h3, .product-title, .product-name") or link
        current_el = card.select_one(".sales .value, [itemprop='price'], [data-price], .price, .product-price")
        if current_el is None:
            current_el = next((node for node in card.select(".value")
                               if node.find_parent(class_="strike-through") is None), None)
        if not (link and title_el and current_el):
            continue
        title = title_el.get_text(" ", strip=True)
        current = _parse_price_node(current_el)
        url = urljoin("https://www.soriana.com" if store == "soriana" else "https://www.liverpool.com.mx", link.get("href", ""))
        if not current or not title or not es_url_producto(url):
            continue
        previous = None
        for old in card.select(".strike-through, del, s, [data-list-price]"):
            previous = _parse_price_node(old)
            if previous and previous > current:
                break
            previous = None
        actual, previous, _ = sanear_par_precios(current, previous, title)
        results.append({"id": card.get("data-pid") or _hash_id(url), "name": title,
            "titulo": title, "brand": "", "url": url, "precio_actual": actual,
            "precio_anterior": previous, "tienda": "Soriana" if store == "soriana" else "Liverpool",
            "tipo_fuente": "ONLINE"})
    return results


def _parsear_html_por_tienda(html_source, tienda, origen_url) -> list[dict]:
    """Compatibilidad para pruebas/consumidores del parser HTML histórico."""
    return _extract_cards(html_source, str(tienda).lower(), origen_url)


try:
    from scrapling.fetchers import FetcherSession
    from scrapling.spiders import Request, Response, Spider
    SCRAPLING_AVAILABLE = True
except ImportError:  # Allows API-first monitor/tests to run without optional scraper extra.
    FetcherSession = Request = Response = Spider = None
    SCRAPLING_AVAILABLE = False


if SCRAPLING_AVAILABLE:
    class MexicoLegacySpider(Spider):
        """Small, bounded spider: one search request per store per cycle."""
        name = "dexter_mexico_legacy"
        logging_level = logging.WARNING
        concurrent_requests = 4
        concurrent_requests_per_domain = 1
        download_delay = 1.0
        robots_txt_obey = True
        autothrottle_enabled = True
        autothrottle_start_delay = 2.0
        autothrottle_max_delay = 3600.0
        autothrottle_block_backoff = True
        max_blocked_retries = 0

        def __init__(self, stores: dict[str, str]):
            super().__init__()
            self.stores = stores

        def configure_sessions(self, manager):
            # Distinct reusable sessions keep light search requests isolated from
            # longer catalog responses. No TLS impersonation or stealth flags.
            manager.add("http", FetcherSession(impersonate=None, stealthy_headers=False, timeout=20, retries=0), default=True)
            manager.add("catalog", FetcherSession(impersonate=None, stealthy_headers=False, timeout=30, retries=0), lazy=True)

        async def start_requests(self):
            for store, template in self.stores.items():
                if _cooldown_active(store):
                    LAST_STATUS[store] = {"state": "cooldown", "reason": _load_cooldowns()[store].get("reason")}
                    continue
                url = template.format(q=quote_plus(TERMINOS[0]))
                sid = "catalog" if store in ("soriana", "liverpool") else "http"
                yield Request(url, sid=sid, callback=self.parse_store, meta={"store": store})

        async def on_error(self, request: Request, error: Exception) -> None:
            store = str((getattr(request, "meta", {}) or {}).get("store", "unknown"))
            error_name = type(error).__name__
            state = "timeout" if "timeout" in error_name.lower() else "network_error"
            LAST_STATUS[store] = {"state": state, "reason": error_name}
            self.logger.warning("[%s] %s; se omite esta tienda en el ciclo.", store, error_name)

        async def is_blocked(self, response: Response) -> bool:
            # Do not let Scrapling's retry queue repeat a WAF/rate-limit request.
            # Non-2xx status still feeds AutoThrottle; parse_store records the exact
            # status and persists the per-store cooldown.
            return False

        async def parse(self, response: Response):
            # Scrapling's required default callback delegates to the per-store parser.
            async for item in self.parse_store(response):
                yield item

        async def parse_store(self, response: Response):
            meta = getattr(response, "meta", {}) or {}
            store_value = meta.get("store", "unknown")
            if isinstance(store_value, dict):
                store_value = store_value.get("store", "unknown")
            store = str(store_value)
            status = int(getattr(response, "status", getattr(response, "status_code", 200)) or 200)
            if status in (403, 412, 429, 503):
                reason, cooldown = _status_reason(status)
                retry_after = _retry_after_seconds(getattr(response, "headers", {}))
                if retry_after is not None:
                    cooldown = max(cooldown, retry_after)
                LAST_STATUS[store] = {"state": "blocked", "http_status": status, "reason": reason, "retry_after_seconds": retry_after}
                _save_cooldown(store, reason, cooldown)
                self.logger.warning("[%s] %s; se aborta esta tienda en este ciclo.", store, reason)
                return
            if status >= 400:
                LAST_STATUS[store] = {"state": "http_error", "http_status": status, "reason": f"HTTP_{status}"}
                return
            raw_body = getattr(response, "body", b"")
            if isinstance(raw_body, bytes):
                body = raw_body.decode("utf-8", errors="replace")
            else:
                body = str(getattr(response, "text", "") or raw_body or "")
            if any(marker in body.lower() for marker in ("access denied", "temporarily blocked", "captcha")):
                LAST_STATUS[store] = {"state": "blocked", "reason": "PERIMETER_CHALLENGE"}
                _save_cooldown(store, "PERIMETER_CHALLENGE", 6 * 60 * 60)
                return
            _clear_cooldown(store)
            rows = _extract_cards(body, store, str(getattr(response, "url", "")))
            LAST_STATUS[store] = {"state": "ok" if rows else "empty", "items": len(rows)}
            for row in rows:
                yield row
else:
    class MexicoLegacySpider:  # pragma: no cover - optional dependency guard
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Scrapling no está instalado; instala requirements-browser.txt")


def buscar_todas(filtro_tienda: str | list[str] | tuple[str, ...] | set[str] | None = None) -> list[dict]:
    """Run Scrapling's async Spider and return normalized product candidates."""
    LAST_STATUS.clear()
    if not SCRAPLING_AVAILABLE:
        configured = _configured_urls()
        if filtro_tienda:
            filters = {str(value).strip().lower() for value in filtro_tienda} if isinstance(filtro_tienda, (list, tuple, set)) else {str(filtro_tienda).strip().lower()}
            configured = {name: url for name, url in configured.items() if name.lower() in filters}
        for store in configured:
            LAST_STATUS[store] = {"state": "not_configured", "reason": "SCRAPLING_NOT_INSTALLED"}
        logger.warning("Scrapling no está instalado; se omite la capa legacy.")
        return []
    stores = _configured_urls()
    if filtro_tienda:
        filtros = {str(value).strip().lower() for value in filtro_tienda} if isinstance(filtro_tienda, (list, tuple, set)) else {str(filtro_tienda).strip().lower()}
        stores = {name: url for name, url in stores.items() if name.lower() in filtros}
    if not stores:
        return []
    try:
        result = MexicoLegacySpider(stores).start()
        items = list(getattr(result, "items", []) or [])
        for store in stores:
            LAST_STATUS.setdefault(store, {"state": "empty", "items": 0})
        return items
    except Exception as exc:
        logger.exception("Spider Scrapling falló (%s); se conserva el resto de fuentes.", type(exc).__name__)
        for store in stores:
            LAST_STATUS.setdefault(store, {"state": "spider_error", "reason": type(exc).__name__})
        return []


def es_url_producto(url, base_url=None):
    """Reject search/listing URLs and accept known direct product route patterns."""
    try:
        parsed = urlparse(url or "")
        host, path = parsed.netloc.lower().split(":", 1)[0], parsed.path.lower()
        if parsed.scheme not in ("http", "https") or not host or not path or path == "/":
            return False
        if base_url:
            base_host = urlparse(base_url).netloc.lower().split(":", 1)[0].removeprefix("www.")
            if base_host and host.removeprefix("www.") != base_host and not host.endswith("." + base_host):
                return False
        if any(x in path for x in ("/search", "/buscar", "/ofertas", "/oferta", "/catalogo", "/marcas", "/home", "/social/")):
            return False
        return any(p in path for p in ("/ip/", "/dp/", "/gp/product/", "/mlm-", "/p/", "/pdp/", "/tienda/pdp/", "/producto/", "/product/", "/item/"))
    except Exception:
        return False
