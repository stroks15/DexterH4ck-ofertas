"""Captura opcional de datos públicos desde un navegador normal.

Este módulo NO intenta evadir CAPTCHA/WAF, modificar fingerprints, usar stealth ni
resolver desafíos. Su objetivo es reutilizar la sesión/cookies que el sitio entrega
normalmente al navegador y capturar respuestas JSON/XHR públicas.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from core.network_json import extract_products


def _sources() -> list[dict[str, Any]]:
    raw = os.getenv("NETWORK_BROWSER_SOURCES", "").strip()
    if not raw:
        return [
            {"store": "Walmart MX", "url": "https://www.walmart.com.mx/search?q=oferta"},
            {"store": "Bodega Aurrera", "url": "https://despensa.bodegaaurrera.com.mx/browse/cupones-y-bonificaciones/rebajas-y-mas/8171461_3848205"},
            {"store": "Soriana", "url": "https://www.soriana.com/ofertas/"},
            {"store": "Liverpool", "url": "https://www.liverpool.com.mx/tienda?s=promociones"},
            {"store": "Suburbia", "url": "https://www.suburbia.com.mx/tienda?s=promociones"},
            {"store": "Suburbia", "url": "https://www.suburbia.com.mx/tienda/ofertas-relampago/catst68781332"},
            {"store": "Amazon MX", "url": "https://www.amazon.com.mx/deals"},
            {"store": "Coppel", "url": "https://www.coppel.com/ofertas"},
            {"store": "Oferstock", "url": "https://www.oferstock.com.mx/"},
        ]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("NetworkBrowser: NETWORK_BROWSER_SOURCES no es JSON válido.")
        return []
    return data if isinstance(data, list) else []


def _json_candidates(text: str) -> list[Any]:
    text = (text or "").strip()
    if not text or len(text) > 8_000_000:
        return []
    candidates = []
    try:
        candidates.append(json.loads(text))
    except Exception:
        pass
    return candidates


def _extract_embedded_json(page, store: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        scripts = page.locator("script").all_text_contents()
    except Exception:
        return rows

    for raw in scripts:
        raw = raw.strip()
        if not raw:
            continue
        if "__NEXT_DATA__" in raw or '"@type":"Product"' in raw or '"@type": "Product"' in raw:
            for payload in _json_candidates(raw):
                rows.extend(extract_products(payload, page.url, store))
    return rows


def buscar_network_browser() -> list[dict[str, Any]]:
    sources = _sources()
    if not sources:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("NetworkBrowser: Playwright no está instalado; fuente omitida.")
        return []

    wait_ms = max(1000, int(os.getenv("NETWORK_BROWSER_WAIT_MS", "6500")))
    max_responses = max(1, int(os.getenv("NETWORK_BROWSER_MAX_RESPONSES", "40")))
    navigation_timeout = max(15_000, int(os.getenv("NETWORK_BROWSER_NAV_TIMEOUT_MS", "30000")))
    results: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for source in sources:
                store = str(source.get("store") or "Browser JSON")
                url = str(source.get("url") or "").strip()
                if not url:
                    continue

                captured = 0
                context = browser.new_context(
                    locale="es-MX",
                    timezone_id="America/Mexico_City",
                    extra_http_headers={
                        "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
                    },
                )
                page = context.new_page()

                def on_response(response):
                    nonlocal captured
                    if captured >= max_responses or response.status >= 400:
                        return
                    resource_type = response.request.resource_type
                    content_type = (response.headers.get("content-type") or "").lower()
                    if "json" not in content_type and resource_type not in ("xhr", "fetch"):
                        return
                    try:
                        raw = response.text()
                        payloads = _json_candidates(raw)
                        if not payloads:
                            return
                        found = []
                        for payload in payloads:
                            found.extend(extract_products(payload, response.url, store))
                        if found:
                            captured += 1
                            results.extend(found)
                    except Exception:
                        return

                page.on("response", on_response)
                try:
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=navigation_timeout,
                    )
                    page.wait_for_timeout(wait_ms)

                    # Las cookies/sesión normales permanecen dentro del contexto
                    # durante toda la navegación de esta fuente.
                    results.extend(_extract_embedded_json(page, store))

                    if response is not None and response.status >= 400:
                        print(
                            f"NetworkBrowser:{store}: navegación HTTP "
                            f"{response.status} en {page.url}"
                        )
                except Exception as exc:
                    print(
                        f"NetworkBrowser:{store}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

        finally:
            browser.close()

    unique: dict[str, dict[str, Any]] = {}
    for item in results:
        key = f"{item.get('tienda', '')}|{item.get('product_id') or item.get('url')}"
        unique[key] = item

    print(f"NetworkBrowser: {len(unique)} productos JSON capturados.")
    return list(unique.values())
