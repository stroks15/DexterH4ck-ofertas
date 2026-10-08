# tools/diagnostico_fuentes.py
"""Diagnóstico de endpoints públicos de tiendas en México.

Usa requests legítimos con User-Agent honesto y clasifica cada código HTTP en un
estado distinto (ok, forbidden, rate_limited, not_found, timeout, ...). No intenta
evadir CAPTCHA/WAF ni suplantar identidad.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from core.source_resilience import STORE_TARGETS, probe_url  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DexterH4ck.Diagnostics")

# Endpoints opcionales configurados por variables de entorno (solo se reporta si existen).
ENV_ENDPOINTS = (
    "WALMART_GRAPHQL_URL", "BODEGA_GRAPHQL_URL", "CHEDRAUI_VTEX_ENDPOINT",
    "SORIANA_API_ENDPOINT", "COPPEL_API_ENDPOINT", "SUBURBIA_API_ENDPOINT",
    "AMAZON_API_ENDPOINT",
)


def probe(session: requests.Session, url: str) -> dict:
    result = probe_url(url, session=session, timeout=15)
    if result.get("final_url") == url:
        result.pop("final_url")
    return result


def main() -> int:
    report: dict = {}
    logger.info("Iniciando diagnóstico de salud de endpoints públicos...")
    with requests.Session() as session:
        for store, urls in STORE_TARGETS.items():
            report[store] = [probe(session, u) for u in urls]
    report["_endpoints_opcionales"] = {
        name: ("configurado" if os.environ.get(name, "").strip() else "no configurado")
        for name in ENV_ENDPOINTS
    }
    Path("diagnostico_fuentes.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== RESUMEN DE DIAGNÓSTICO DE RED ===")
    for store, rows in report.items():
        if store.startswith("_"):
            continue
        print(f"{store}: " + ", ".join(f"{r['state']}={r.get('status')}" for r in rows))
    for name, estado in report["_endpoints_opcionales"].items():
        print(f"{name}: {estado}")
    # El diagnóstico informa; un bloqueo individual no es un fallo global.
    return 0


if __name__ == "__main__":
    sys.exit(main())
