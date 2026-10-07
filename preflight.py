import importlib
import json
import os
import py_compile
import time
from pathlib import Path
from urllib.parse import quote_plus

from curl_cffi import requests as curl_requests

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
    "core/source_resilience.py",
    "scrapers/api_stores.py",
    "config/walmart_graphql_query.py",
    "scrapers/walmart_graphql.py",
    "scrapers/bodega_graphql.py",
    "scrapers/walmart_api.py",
    "scrapers/bodega_aurrera_api.py",
    "scrapers/mercado_libre_api.py",
    "scrapers/feeds_comunidad_api.py",
    "scrapers/liquidazona.py",
    "scrapers/comunidades_web.py",
]

SOURCES = {
    "Walmart MX": "https://walmart.com.mx",
    "Bodega Aurrera": "https://bodegaaurrera.com.mx",
    "Chedraui": "https://chedraui.com.mx",
    "Mercado Libre MX": "https://mercadolibre.com.mx",
    "Soriana": "https://soriana.com",
    "Liverpool": "https://liverpool.com.mx",
    "Amazon MX": "https://amazon.com.mx",
    "Coppel": "https://coppel.com",
    "Suburbia": "https://suburbia.com.mx",
    "Oferstock": "https://oferstock.com.mx",
    "Google": "https://google.com",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
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
        "scrapers.liquidazona",
        "scrapers.comunidades_web",
        "core.source_resilience",
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
            if status in (401, 403, 412):
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
        except Exception as exc:
            if attempt == 0:
                time.sleep(1)
                continue
            return {"status": None, "state": "network_error", "error": type(exc).__name__}
    return {"status": None, "state": "unknown"}

def main():
    report = {"python_errors": check_python(), "import_errors": [], "sources": {}, "skipped_sources": []}
    if not report["python_errors"]:
        report["import_errors"] = check_imports()

    # Sesión robusta de curl_cffi con emulación de Chrome para pasar desapercibido en preflight
    session = curl_requests.Session(impersonate="chrome")
    session.headers.update(HEADERS)
    
    for name, url in SOURCES.items():
        result = check_http(session, url)
        report["sources"][name] = result
        if result["state"] in {"rate_limited", "blocked", "server_error", "network_error", "blocked_content"}:
            if not (
                name in {"Walmart MX", "Bodega Aurrera", "Amazon MX", "Mercado Libre MX"}
                and result["state"] == "blocked_content"
            ):
                report["skipped_sources"].append(name)

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

    return 1 if report["python_errors"] or report["import_errors"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
