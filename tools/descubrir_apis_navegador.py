"""Descubre endpoints JSON que usa una página mediante Playwright.

Uso manual:
NETWORK_DISCOVERY_URL=https://www.soriana.com/buscar?q=ofertas python tools/descubrir_apis_navegador.py

Genera network_endpoints.json. No guarda cookies ni tokens de autenticación.
"""
from __future__ import annotations
import json,os
from pathlib import Path

from core.network_json import extract_products

def main():
    url=os.getenv("NETWORK_DISCOVERY_URL","").strip()
    if not url:
        raise SystemExit("Falta NETWORK_DISCOVERY_URL")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("Instala requirements-browser.txt y Chromium de Playwright.")
    rows=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(locale="es-MX",extra_http_headers={"Accept-Language":"es-MX,es;q=0.9,en;q=0.7"})
        def on_response(response):
            ct=(response.headers.get("content-type") or "").lower()
            if response.status>=400 or ("json" not in ct and response.request.resource_type not in ("xhr","fetch")):
                return
            try: payload=response.json()
            except Exception: return
            products=extract_products(payload,response.url,"Discovery")
            rows.append({
                "method":response.request.method,
                "url":response.url,
                "status":response.status,
                "content_type":ct,
                "product_candidates":len(products),
                "product_ids":[str(x.get("product_id") or "") for x in products[:10]],
            })
        page.on("response",on_response)
        page.goto(url,wait_until="domcontentloaded",timeout=30000)
        page.wait_for_timeout(max(0,int(os.getenv("NETWORK_DISCOVERY_WAIT_MS","7000"))))
        browser.close()
    unique={}
    for row in rows:
        unique[row["url"]]=row
    Path("network_endpoints.json").write_text(json.dumps(list(unique.values()),ensure_ascii=False,indent=2),encoding="utf-8")
    for row in unique.values():
        print(f'{row["status"]} {row["method"]} products={row["product_candidates"]} {row["url"]}')
    print(f"Endpoints JSON detectados: {len(unique)}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
