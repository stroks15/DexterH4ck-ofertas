# preflight.py
import os
import logging
# Corregido el import de curl_cffi que rompía el inicio del script
from curl_cffi import requests as curl_requests

logger = logging.getLogger("DexterH4ck.Preflight")

HEADERS_PROV = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache"
}

def ejecutar_diagnostico_conectividad(url: str, nombre_tienda: str) -> str:
    """
    Realiza una comprobación de salud de red simulando el stack completo de Chrome
    para evitar generar alertas falsas de bloqueo en el runner de GitHub.
    """
    session = curl_requests.Session(impersonate="chrome")
    try:
        # Se establece un timeout fijo para mitigar congelamientos de red
        response = session.get(url, headers=HEADERS_PROV, timeout=15.0, allow_redirects=True)
        html_lower = response.text.lower()
        
        if any(term in html_lower for term in ["access denied", "temporarily blocked", "captcha"]):
            logger.warning(f"[{nombre_tienda}] El servidor devolvió contenido con firmas de bloqueo o reto.")
            return "blocked"
            
        if response.status_code == 200:
            return "ok"
        elif response.status_code >= 500:
            return "server_error"
        else:
            return "http_error"
    except Exception as e:
        logger.error(f"[{nombre_tienda}] Error en el chequeo HTTP del preflight: {str(e)}")
        return "network_error"
