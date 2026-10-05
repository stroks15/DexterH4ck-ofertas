import importlib
import json
import os
import py_compile
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

TIMEOUT = 12
REPORT = Path("preflight_health.json")
ENV_FILE = Path("preflight.env")

PY_FILES = [
    "monitor_ofertas.py",
    "scrapers/tiendas_mexico.py",
    "scrapers/telegram_ofertas.py",
    "scrapers/liquidaciones_oficiales.py",
    "scrapers/tiendas_fisicas.py",
    "core/ai_reparador.py",
    "core/liquidation_engine.py",
    "core/extreme_liquidation.py",
    "core/scraper_base.py",
    "scrapers/api_stores.py",
    "config/walmart_graphql_query.py",
    "scrapers/walmart_graphql.py",
    "scrapers/bodega_graphql.py",
    "scrapers/walmart_api.py",
    "scrapers/bodega_aurrera_api.py",
    "scrapers/mercado_libre_api.py",
    "scrapers/feeds_comunidad_api.py",
]

SOURCES = {
    "Walmart MX": "https://www.walmart.com.mx/content/especiales/360013_300279",
    "Bodega Aurrera": "https://www.bodegaaurrera.com.mx/browse/eventos/remates/remates-para-tu-hogar/490004_1030001_1030004",
    "Chedraui": "https://www.chedraui.com.mx/promociones/solo-hoy",
    "Mercado Libre MX": "https://listado.mercadolibre.com.mx/celular",
    "Soriana": "https://www.soriana.com/buscar?q=ofertas",
    "Liverpool": "https://www.liverpool.com.mx/tienda?s=ofertas+de+liquidaci%C3%B3n",
    "Amazon MX": "https://www.amazon.com.mx/",
    "Coppel": "https://www.coppel.com/ofertas",
    "Suburbia": "https://www.suburbia.com.mx/tienda?s=promociones",
    "Oferstock": "https://www.oferstock.com.mx/",
    "Google": "https://www.google.com/search?q=site%3Asoriana.com+ofertas",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
}

def check_python():
    errors = []
    for path in PY_FILES:
        try:
            py_compile.compile(path, doraise=True)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    return errors

def check_imports():
    errors = []
    modules = [
        "monitor_ofertas",
        "scrapers.tiendas_mexico",
        "scrapers.telegram_ofertas",
        "scrapers.liquidaciones_oficiales",
        "scrapers.tiendas_fisicas",
        "core.ai_reparador",
        "core.liquidation_engine",
        "core.extreme_liquidation",
        "core.scraper_base",
        "scrapers.api_stores",
        "config.walmart_graphql_query",
        "scrapers.walmart_graphql",
        "scrapers.bodega_graphql",
        "scrapers.walmart_api",
        "scrapers.bodega_aurrera_api",
        "scrapers.mercado_libre_api",
        "scrapers.feeds_comunidad_api",
    ]
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"{module}: {type(exc).__name__}: {exc}")
    return errors

def check_http(session, url):
    for attempt in range(2):
        try:
            response = session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            status = response.status_code
            if status == 429:
                return {"status": status, "state": "rate_limited", "retry_after": response.headers.get("Retry-After")}
            if status in (401, 403):
                return {"status": status, "state": "blocked"}
            if status >= 500:
                if attempt == 0:
                    time.sleep(2)
                    continue
                return {"status": status, "state": "server_error"}
            if status >= 400:
                return {"status": status, "state": "http_error"}
            text = response.text[:50000].lower()
            blocked = any(x in text for x in ("access denied", "captcha", "temporarily blocked", "too many requests"))
            return {"status": status, "state": "blocked_content" if blocked else "ok"}
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(1)
                continue
            return {"status": None, "state": "network_error", "error": type(exc).__name__}
    return {"status": None, "state": "unknown"}

def main():
    report = {"python_errors": check_python(), "import_errors": [], "sources": {}, "skipped_sources": []}
    if not report["python_errors"]:
        report["import_errors"] = check_imports()

    session = requests.Session()
    session.headers.update(HEADERS)
    for name, url in SOURCES.items():
        result = check_http(session, url)
        report["sources"][name] = result
        # Walmart/Bodega pueden devolver 200 con una capa anti-bot. No se omiten:
        # sus adaptadores tienen fallback por índice público y fuente oficial.
        if result["state"] in {"rate_limited", "blocked", "server_error", "network_error", "blocked_content"}:
            if not (
                name in {"Walmart MX", "Bodega Aurrera", "Amazon MX", "Mercado Libre MX"}
                and result["state"] == "blocked_content"
            ):
                report["skipped_sources"].append(name)

    # Google is only a fallback for physical-store discovery. If limited, disable
    # that fallback instead of allowing a cascade of 429s.
    skip = set(report["skipped_sources"])
    env_lines = [f"PREFLIGHT_SKIP_SOURCES={','.join(sorted(skip))}"]
    ENV_FILE.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== PREFLIGHT ===")
    print("Python:", "OK" if not report["python_errors"] else "ERROR")
    print("Imports:", "OK" if not report["import_errors"] else "ERROR")
    for name, result in report["sources"].items():
        print(f"{name}: {result['state']} ({result.get('status')})")
    print("Fuentes que se omitirán este ciclo:", ", ".join(sorted(skip)) or "ninguna")

    # Syntax/import errors are real deployment errors; source 429/403 is not a
    # code failure and is handled by source-level circuit breaking.
    return 1 if report["python_errors"] or report["import_errors"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
