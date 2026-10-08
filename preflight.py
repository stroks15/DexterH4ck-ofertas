# preflight.py
"""Preflight por tienda.

Sondea cada tienda con requests legítimos (User-Agent honesto) y clasifica el
resultado. Una tienda bloqueada/no disponible se OMITE (PREFLIGHT_BLOCKED_STORES),
pero jamás detiene el resto del monitor: este script siempre termina con código 0
y deja siempre `preflight_health.json` y `preflight.env`.
No intenta evadir CAPTCHA/WAF.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from core.source_resilience import (
    STORE_TARGETS,
    classify_status,
    probe_url,
    summarize_store,
    BLOCK_SIGNATURES,
    PROBE_HEADERS,
)

logger = logging.getLogger("DexterH4ck.Preflight")

HEALTH_FILE = "preflight_health.json"
ENV_FILE = "preflight.env"


def ejecutar_diagnostico_conectividad(url: str, nombre_tienda: str) -> str:
    """Compatibilidad: devuelve el estado clasificado de una URL ('ok', 'blocked', ...)."""
    resultado = probe_url(url)
    if resultado["state"] != "ok":
        logger.warning("[%s] preflight %s -> %s (HTTP %s)", nombre_tienda, url, resultado["state"], resultado.get("status"))
    return resultado["state"]


def ejecutar_preflight(targets: dict[str, list[str]] | None = None) -> dict:
    targets = targets or STORE_TARGETS
    tiendas: dict[str, dict] = {}

    def _una(tienda: str) -> tuple[str, dict]:
        with requests.Session() as session:
            probes = [probe_url(u, session=session) for u in targets[tienda]]
        return tienda, summarize_store(probes)

    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="preflight") as pool:
        for tienda, resumen in pool.map(_una, list(targets)):
            tiendas[tienda] = resumen
            nivel = logging.INFO if resumen["state"] == "ok" else logging.WARNING
            logger.log(nivel, "[%s] preflight: %s%s", tienda, resumen["state"],
                       " (se omitirá en este ciclo)" if resumen["blocked"] else "")

    bloqueadas = [t for t, r in tiendas.items() if r["blocked"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stores": tiendas,
        "blocked_stores": bloqueadas,
    }


def escribir_salidas(health: dict) -> None:
    with open(HEALTH_FILE, "w", encoding="utf-8") as fh:
        json.dump(health, fh, ensure_ascii=False, indent=2)
    with open(ENV_FILE, "w", encoding="utf-8") as fh:
        fh.write(f"PREFLIGHT_BLOCKED_STORES={','.join(health.get('blocked_stores', []))}\n")


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        health = ejecutar_preflight()
    except Exception as exc:  # el preflight no debe convertir un fallo propio en fallo global
        logger.error("Preflight falló de forma inesperada (%s: %s); no se bloquea ninguna fuente.", type(exc).__name__, exc)
        health = {"generated_at": datetime.now(timezone.utc).isoformat(), "stores": {},
                  "blocked_stores": [], "error": f"{type(exc).__name__}: {exc}"}
    escribir_salidas(health)
    print("=== PREFLIGHT ===")
    for tienda, r in health["stores"].items():
        print(f"{tienda}: {r['state']}{' [OMITIDA]' if r['blocked'] else ''}")
    if health["blocked_stores"]:
        print(f"::warning::Tiendas omitidas en este ciclo por preflight: {', '.join(health['blocked_stores'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
