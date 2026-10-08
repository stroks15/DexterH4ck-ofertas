# tools/descubrir_apis_navegador.py
"""Descubre endpoints JSON que una página pública consulta al cargarse.

Usa Playwright normal (sin stealth ni ocultar la automatización). Solo registra qué
hosts/URLs JSON se piden; no evade CAPTCHA/WAF.
"""
import asyncio
import json
import logging
import os
from urllib.parse import urlparse

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DexterH4ck.ApiDiscover")


async def descubrir_endpoints():
    url_objetivo = os.environ.get("NETWORK_DISCOVERY_URL")
    try:
        wait_ms = int(os.environ.get("NETWORK_DISCOVERY_WAIT_MS", "7000"))
    except (ValueError, TypeError):
        wait_ms = 7000

    if not url_objetivo:
        logger.error("Falta la variable de entorno NETWORK_DISCOVERY_URL. Abortando.")
        return

    logger.info("Iniciando inspección de red sobre la URL: %s", url_objetivo)
    endpoints_capturados = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(locale="es-MX", timezone_id="America/Mexico_City")
        page = await context.new_page()

        async def interceptar_respuesta(response):
            try:
                content_type = response.headers.get("content-type", "").lower()
                # Solo tráfico útil: llamadas REST JSON o GraphQL
                if "application/json" in content_type or "graphql" in response.url:
                    url_completa = response.url
                    host = urlparse(url_completa).netloc
                    lista = endpoints_capturados.setdefault(host, [])
                    if url_completa not in lista:
                        lista.append(url_completa)
                        logger.info("[CAPTURA JSON] Endpoint en %s -> %s...", host, url_completa[:90])
            except Exception as exc:
                logger.debug("Respuesta no inspeccionable: %s", exc)

        page.on("response", interceptar_respuesta)

        try:
            respuesta = await page.goto(url_objetivo, wait_until="domcontentloaded", timeout=45000)
            if respuesta is not None and respuesta.status >= 400:
                logger.warning("La página respondió HTTP %s; el resultado puede estar incompleto.", respuesta.status)
            logger.info("Página cargada. Esperando %s ms para capturar peticiones asíncronas...", wait_ms)
            await asyncio.sleep(wait_ms / 1000.0)
        except Exception as e:
            logger.error("Error o timeout durante el renderizado: %s", e)
        finally:
            await context.close()
            await browser.close()

    with open("network_endpoints.json", "w", encoding="utf-8") as f:
        json.dump(endpoints_capturados, f, ensure_ascii=False, indent=2)
    logger.info("Archivo 'network_endpoints.json' generado con %d hosts.", len(endpoints_capturados))


if __name__ == "__main__":
    asyncio.run(descubrir_endpoints())
