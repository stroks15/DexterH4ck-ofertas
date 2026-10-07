"""Captura opcional de respuestas JSON desde el navegador.

Es un último recurso. Observa fetch/XHR normales de la página y no usa stealth,
CAPTCHA solving, rotación de proxies ni técnicas para eludir controles.
"""
from __future__ import annotations
import json,os
from typing import Any
from core.network_json import extract_products

def _sources()->list[dict[str,Any]]:
    raw=os.getenv("NETWORK_BROWSER_SOURCES","").strip()
    if not raw:
        return [
            {"store": "Walmart MX", "url": "https://www.walmart.com.mx/search?q=oferta"},
            {"store": "Bodega Aurrera", "url": "https://despensa.bodegaaurrera.com.mx/browse/cupones-y-bonificaciones/rebajas-y-mas/8171461_3848205"},
            {"store": "Soriana", "url": "https://www.soriana.com/ofertas/"},
            {"store": "Liverpool", "url": "https://www.liverpool.com.mx/tienda?s=promociones"},
            {"store": "Suburbia", "url": "https://www.suburbia.com.mx/tienda/ofertas-relampago/catst68781332"},
            {"store": "Amazon MX", "url": "https://www.amazon.com.mx/deals"},
            {"store": "Coppel", "url": "https://www.coppel.com/ofertas"},
            {"store": "Oferstock", "url": "https://www.oferstock.com.mx/"},
        ]
    try: data=json.loads(raw)
    except json.JSONDecodeError:
        print("NetworkBrowser: NETWORK_BROWSER_SOURCES no es JSON válido."); return []
    return data if isinstance(data,list) else []

def buscar_network_browser():
    sources=_sources()
    if not sources: return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("NetworkBrowser: Playwright no está instalado; fuente omitida."); return []
    wait_ms=max(0,int(os.getenv("NETWORK_BROWSER_WAIT_MS","6500")))
    max_responses=max(1,int(os.getenv("NETWORK_BROWSER_MAX_RESPONSES","40")))
    results=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True)
        try:
            for source in sources:
                store=str(source.get("store") or "Browser JSON")
                url=str(source.get("url") or "").strip()
                if not url: continue
                captured=0
                page=browser.new_page(locale="es-MX",extra_http_headers={"Accept-Language":"es-MX,es;q=0.9,en;q=0.7"})
                def on_response(response):
                    nonlocal captured
                    if captured>=max_responses or response.status>=400: return
                    content_type=(response.headers.get("content-type") or "").lower()
                    if "json" not in content_type and response.request.resource_type not in ("xhr","fetch"): return
                    try: payload=response.json()
                    except Exception: return
                    rows=extract_products(payload,response.url,store)
                    if rows:
                        captured+=1; results.extend(rows)
                page.on("response",on_response)
                try:
                    page.goto(url,wait_until="domcontentloaded",timeout=30000)
                    page.wait_for_timeout(wait_ms)
                except Exception as exc:
                    print(f"NetworkBrowser:{store}: {type(exc).__name__}: {exc}")
                finally: page.close()
        finally: browser.close()
    unique={}
    for item in results:
        key=f"{item.get('tienda','')}|{item.get('product_id') or item.get('url')}"
        unique[key]=item
    print(f"NetworkBrowser: {len(unique)} productos JSON capturados.")
    return list(unique.values())
