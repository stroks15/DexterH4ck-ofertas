"""Descubrimiento híbrido conservador: JSON-LD/HTML, Playwright y Scrapling.

La capa es opcional y no sustituye las APIs verificadas. Solo consulta URLs
públicas configuradas, respeta bloqueos y devuelve productos con evidencia.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.network_json import extract_products
from core.source_resilience import classify_status

logger = logging.getLogger("DexterH4ck.Hybrid")
LAST_STATUS: dict[str, Any] = {"state": "not_run", "layers": {}}


def _json_ld(html: str, base_url: str, store: str) -> list[dict[str, Any]]:
    rows=[]
    soup=BeautifulSoup(html or "", "html.parser")
    for node in soup.select('script[type="application/ld+json"]'):
        raw=node.string or node.get_text()
        try: payload=json.loads(raw)
        except (TypeError, ValueError): continue
        rows.extend(extract_products(payload, base_url, store))
    return rows


def extract_structured_html(html: str, base_url: str, store: str) -> list[dict[str, Any]]:
    """Extrae JSON-LD y metadatos embebidos sin inferir precios desde texto libre."""
    rows=_json_ld(html, base_url, store)
    soup=BeautifulSoup(html or "", "html.parser")
    meta={}
    for tag in soup.select('meta[property], meta[name]'):
        key=tag.get("property") or tag.get("name")
        value=tag.get("content")
        if key and value: meta[key]=value
    if meta.get("og:title") and meta.get("og:url") and meta.get("product:price:amount"):
        payload={"name":meta["og:title"],"url":urljoin(base_url,meta["og:url"]),
                 "price":meta["product:price:amount"],
                 "priceCurrency":meta.get("product:price:currency","MXN")}
        rows.extend(extract_products(payload,base_url,store))
    unique={}
    for row in rows:
        key=str(row.get("product_id") or row.get("url") or "").lower()
        if key: unique[key]=row
    return list(unique.values())


def _configured_sources() -> list[dict[str,str]]:
    raw=os.getenv("HYBRID_STRUCTURED_SOURCES", "").strip()
    if not raw: return []
    try: data=json.loads(raw)
    except ValueError:
        logger.warning("HYBRID_STRUCTURED_SOURCES no es JSON válido")
        return []
    return [x for x in data if isinstance(x,dict) and x.get("url")] if isinstance(data,list) else []


def discover_structured_http() -> list[dict[str,Any]]:
    import requests
    from core.scraper_base import DEFAULT_HEADERS
    rows=[]
    for source in _configured_sources():
        url=str(source["url"]).strip(); store=str(source.get("store") or "Structured JSON-LD")
        try:
            response=requests.get(url,headers=DEFAULT_HEADERS,timeout=15)
            state=classify_status(response.status_code)
            if state != "ok":
                logger.warning("[%s] structured HTTP %s (%s)",store,response.status_code,state)
                continue
            rows.extend(extract_structured_html(response.text,url,store))
        except requests.RequestException as exc:
            logger.warning("[%s] structured network error: %s",store,type(exc).__name__)
    return rows


def discover_playwright() -> list[dict[str,Any]]:
    """Captura JSON público y JSON-LD con Playwright; sin stealth ni evasión."""
    raw=os.getenv("HYBRID_PLAYWRIGHT_SOURCES", "").strip()
    if not raw: return []
    try: sources=json.loads(raw)
    except ValueError: return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("Playwright no instalado; capa omitida")
        return []
    rows=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            for source in sources if isinstance(sources,list) else []:
                if not isinstance(source,dict) or not source.get("url"): continue
                store=str(source.get("store") or "Playwright")
                page=browser.new_page(locale="es-MX")
                try:
                    captured=[]
                    def on_response(resp):
                        if resp.status >= 400: return
                        ct=(resp.headers.get("content-type") or "").lower()
                        if "json" not in ct and resp.request.resource_type not in ("xhr","fetch"): return
                        try: captured.append(resp.json())
                        except Exception: pass
                    page.on("response",on_response)
                    response=page.goto(str(source["url"]),wait_until="domcontentloaded",timeout=30000)
                    page.wait_for_timeout(min(10000,max(1000,int(os.getenv("HYBRID_PLAYWRIGHT_WAIT_MS","2500")))))
                    if response and response.status < 400:
                        rows.extend(extract_structured_html(page.content(),page.url,store))
                    for payload in captured: rows.extend(extract_products(payload,page.url,store))
                except Exception as exc:
                    logger.warning("[%s] Playwright: %s",store,type(exc).__name__)
                finally: page.close()
        finally: browser.close()
    unique={}
    for row in rows:
        key=str(row.get("product_id") or row.get("url") or "").lower()
        if key: unique[key]=row
    return list(unique.values())


def discover_hybrid() -> list[dict[str,Any]]:
    """Combina capas estructuradas y Playwright; Scrapling sigue en su adaptador."""
    global LAST_STATUS
    structured=discover_structured_http()
    browser=discover_playwright()
    rows=structured+browser
    unique={}
    for row in rows:
        key=str(row.get("tienda") or "")+"|"+str(row.get("product_id") or row.get("url") or "")
        if key != "|": unique[key]=row
    result=list(unique.values())
    LAST_STATUS={"state": "ok" if result else ("not_configured" if not (_configured_sources() or os.getenv("HYBRID_PLAYWRIGHT_SOURCES", "").strip()) else "empty"),
                 "count": len(result), "layers": {"structured": len(structured), "playwright": len(browser)}}
    return result
