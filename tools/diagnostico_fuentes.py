# tools/diagnostico_fuentes.py
"""Diagnóstico de endpoints públicos de tiendas en México.
Utiliza firmas TLS/JA3 indetectables mediante curl_cffi para evitar falsos
positivos de bloqueo en el runner y clasifica los estados de red.
"""
from __future__ import annotations
import json
import logging
import random
from pathlib import Path
from curl_cffi import requests as curl_requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DexterH4ck.Diagnostics")

TARGETS = {
    "Walmart MX": [
        "https://walmart.com.mx",
        "https://walmart.com.mx"
    ],
    "Bodega Aurrera": [
        "https://bodegaaurrera.com.mx",
        "https://bodegaaurrera.com.mx"
    ],
    "Soriana": [
        "https://soriana.com",
        "https://soriana.com"
    ],
    "Chedraui": [
        "https://chedraui.com.mx"
    ],
    "Mercado Libre MX": [
        "https://mercadolibre.com",
        "https://mercadolibre.com.mx"
    ],
    "Amazon MX": [
        "https://amazon.com.mx",
        "https://amazon.com.mx"
    ],
}

USER_AGENTS_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def probe(session: curl_requests.Session, url: str) -> dict:
    headers = {
        "User-Agent": random.choice(USER_AGENTS_POOL),
        "Accept": "text/html,application/json,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive"
    }
    try:
        # Se establece impersonate='chrome' para evadir retos perimetrales automatizados en el diagnóstico
        r = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        
        if r.status_code < 400:
            state = "ok"
        elif r.status_code in (401, 403, 412, 429):
            state = "forbidden"
        elif r.status_code == 404:
            state = "not_found"
        elif r.status_code == 429:
            state = "rate_limited"
        elif r.status_code >= 500:
            state = "server_error"
        else:
            state = "http_error"
            
        return {"status": r.status_code, "final_url": r.url, "state": state}
    except Exception as exc:
        return {"status": None, "state": "network_error", "error": type(exc).__name__}

def main() -> int:
    report = {}
    logger.info("Iniciando escaneo perimetral de salud de endpoints públicos...")
    
    # Se inicializa el cliente robusto unificado con suplantación de firmas
    with curl_requests.Session(impersonate="chrome") as session:
        for store, urls in TARGETS.items():
            report[store] = [{"url": u, **probe(session, u)} for u in urls]
            
    # Escritura segura del reporte serializado para el artefacto de GitHub Actions
    Path("diagnostico_fuentes.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print("\n=== RESUMEN DE DIAGNÓSTICO DE RED ===")
    for store, rows in report.items():
        # CORREGIDO: Alternamos comillas triples externas para anidar las comillas de los diccionarios limpiamente sin backslashes
        resumen_tienda = ", ".join(f"{r['state']}={r.get('status')}" for r in rows)
        print(f"{store}: {resumen_tienda}")
        
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
