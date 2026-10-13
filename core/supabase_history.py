"""Historial centralizado de alertas 95-99 % en Supabase (opcional).

Sirve para que la regla "una liquidación extrema se envía una sola vez" sobreviva a
rebases, ejecuciones efímeras de CI/CD y pérdida del ``historial_ofertas.json`` local.

Configuración (todo por entorno, nunca en texto plano en el repo):
    SUPABASE_URL                  https://<proyecto>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY     clave de servicio (o SUPABASE_KEY como alternativa)

Sin esas variables el módulo queda deshabilitado y el monitor usa solo el historial local.
Ninguna función lanza excepciones ni escribe la URL/clave en los logs.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger("DexterH4ck.Supabase")

TABLA = "alertas_enviadas"
TIMEOUT = 8


def _config() -> tuple[str, str] | None:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    clave = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
             or os.environ.get("SUPABASE_KEY", "").strip())
    if not url or not clave or not url.lower().startswith("https://"):
        return None
    return url, clave


def habilitado() -> bool:
    return _config() is not None


def _headers(clave: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "apikey": clave,
        "Authorization": f"Bearer {clave}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def ya_enviada(clave_oferta: str) -> bool | None:
    """True si la oferta ya se envió, False si no, None si no se pudo comprobar.

    ``None`` significa "desconocido" (Supabase apagado o con error): quien llama debe
    apoyarse en el historial local en ese caso.
    """
    cfg = _config()
    if cfg is None or not clave_oferta:
        return None
    url, clave = cfg
    try:
        response = requests.get(
            f"{url}/rest/v1/{TABLA}",
            params={"select": "clave", "clave": f"eq.{clave_oferta}", "limit": "1"},
            headers=_headers(clave),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        if response.status_code != 200:
            logger.warning("Supabase: consulta de historial respondió HTTP %s.", response.status_code)
            return None
        datos = response.json()
        return isinstance(datos, list) and len(datos) > 0
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Supabase: no se pudo consultar el historial (%s).", type(exc).__name__)
        return None


def registrar_envio(clave_oferta: str, item: dict[str, Any], descuento: Any) -> bool:
    """Guarda que la oferta ya se envió. Devuelve True si quedó registrada."""
    cfg = _config()
    if cfg is None or not clave_oferta:
        return False
    url, clave = cfg
    fila = {
        "clave": clave_oferta,
        "tienda": str(item.get("tienda") or "")[:80],
        "producto": str(item.get("titulo") or item.get("nombre") or "")[:240],
        "precio": item.get("precio_actual"),
        "precio_anterior": item.get("precio_anterior"),
        "descuento": descuento,
        "url": str(item.get("url") or "")[:1000],
    }
    try:
        response = requests.post(
            f"{url}/rest/v1/{TABLA}",
            json=fila,
            headers=_headers(clave, {"Prefer": "resolution=ignore-duplicates,return=minimal"}),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        if response.status_code in (200, 201, 204):
            return True
        logger.warning("Supabase: registro de envío respondió HTTP %s.", response.status_code)
        return False
    except requests.RequestException as exc:
        logger.warning("Supabase: no se pudo registrar el envío (%s).", type(exc).__name__)
        return False


__all__ = ["habilitado", "ya_enviada", "registrar_envio", "TABLA"]
