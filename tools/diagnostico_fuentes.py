"""Diagnóstico de endpoints públicos de tiendas.
No intenta saltar CAPTCHA/WAF; clasifica respuestas para que el monitor use
fallbacks y permite revisar el estado como artefacto de GitHub Actions.
"""
from __future__ import annotations
import json
from pathlib import Path
import requests

TARGETS={
"Walmart MX":["https://www.walmart.com.mx/content/especiales/360013_300279","https://www.walmart.com.mx/search?q=oferta"],
"Bodega Aurrera":["https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-para-tu-hogar/490004_1030001_1030004","https://www.bodegaaurrera.com.mx/search?q=oferta"],
"Soriana":["https://www.soriana.com/buscar?q=ofertas","https://www.soriana.com/buscar?q=productos"],
"Chedraui":["https://www.chedraui.com.mx/api/catalog_system/pub/products/search?_from=0&_to=4&O=OrderByBestDiscountDESC"],
"Mercado Libre MX":["https://api.mercadolibre.com/sites/MLM/search?q=oferta&limit=1","https://listado.mercadolibre.com.mx/oferta"],
"Amazon MX":["https://www.amazon.com.mx/s?k=liquidacion","https://www.amazon.com.mx/deals"],
}
HEADERS={"User-Agent":"DexterH4ck-ofertas/1.0 (+resilient-monitor)","Accept-Language":"es-MX,es;q=0.9,en;q=0.7","Accept":"text/html,application/json;q=0.9,*/*;q=0.8"}

def probe(session,url):
    try:
        r=session.get(url,headers=HEADERS,timeout=12,allow_redirects=True)
        if r.status_code<400: state="ok"
        elif r.status_code in (401,403): state="forbidden"
        elif r.status_code==404: state="not_found"
        elif r.status_code==429: state="rate_limited"
        elif r.status_code>=500: state="server_error"
        else: state="http_error"
        return {"status":r.status_code,"final_url":r.url,"state":state}
    except requests.RequestException as exc:
        return {"status":None,"state":"network_error","error":type(exc).__name__}

def main():
    report={}
    with requests.Session() as session:
        for store,urls in TARGETS.items():
            report[store]=[{"url":u,**probe(session,u)} for u in urls]
    Path("diagnostico_fuentes.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    for store,rows in report.items():
        print(store+": "+", ".join(f"{r['state']}={r.get('status')}" for r in rows))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
