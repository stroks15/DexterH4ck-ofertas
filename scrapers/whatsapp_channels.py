# scrapers/whatsapp_channels.py
"""Lectura de las vistas web públicas de canales de WhatsApp con Playwright normal.

No usa stealth, no modifica fingerprints ni oculta la automatización. Si el
navegador no está disponible o el canal responde con error/bloqueo, se reporta en
LAST_STATUS y el resto del monitor continúa.
"""
import asyncio
import hashlib
import html
import json
import logging
import os
import re

from bs4 import BeautifulSoup

from core.source_resilience import classify_status

logger = logging.getLogger("DexterH4ck.WhatsApp")

LAST_STATUS: dict = {}
BLOCK_SIGNATURES = ("access denied", "temporarily blocked", "captcha")


class WhatsAppChannelScraper:
    def __init__(self, context=None):
        self.context = context
        # Archivo de configuración con los canales objetivo
        self.config_path = "config/fuentes_liquidaciones.json"

    def _cargar_canales(self) -> list:
        if not os.path.exists(self.config_path):
            logger.error("[WHATSAPP] Archivo de configuración ausente en: %s", self.config_path)
            return []
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("fuentes", []) if isinstance(data, dict) else []
        except (OSError, json.JSONDecodeError) as e:
            logger.error("[WHATSAPP] Error al leer fuentes JSON: %s", e)
            return []

    async def buscar_canales_async(self) -> list:
        """Abre cada canal público en un navegador normal y extrae enlaces de los mensajes."""
        from playwright.async_api import async_playwright

        candidatos_encontrados = []
        canales = [c for c in self._cargar_canales()
                   if isinstance(c, dict) and str(c.get("tipo", "")).lower() == "whatsapp_channel"]
        if not canales:
            LAST_STATUS["state"] = "not_configured"
            return candidatos_encontrados

        try:
            wait_ms = int(getattr(self.context, "NETWORK_BROWSER_WAIT_MS", os.getenv("NETWORK_BROWSER_WAIT_MS", "7000")))
        except (ValueError, TypeError):
            wait_ms = 7000

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(locale="es-MX", timezone_id="America/Mexico_City")
            page = await context.new_page()
            estados = []
            try:
                for canal in canales:
                    nombre = canal.get("nombre", "Canal Anónimo")
                    url = canal.get("url")
                    if not url:
                        continue
                    try:
                        logger.info("[WHATSAPP] Leyendo canal público: '%s'", nombre)
                        respuesta = await page.goto(url, wait_until="networkidle", timeout=35000)
                        if respuesta is not None and respuesta.status >= 400:
                            logger.warning("[WHATSAPP] Canal '%s': HTTP %s", nombre, respuesta.status)
                            estados.append(classify_status(respuesta.status))
                            continue
                        await asyncio.sleep(wait_ms / 1000.0)
                        html_source = await page.content()
                        if any(sig in html_source.lower() for sig in BLOCK_SIGNATURES):
                            logger.warning("[WHATSAPP] Canal '%s': reto/bloqueo detectado; se omite.", nombre)
                            estados.append("blocked")
                            continue
                        soup = BeautifulSoup(html_source, "html.parser")
                        bloques = soup.select("[dir='ltr'], .whatsapp-public-message-text")
                        estados.append("ok")
                        for bloque in bloques:
                            texto = bloque.get_text(" ", strip=True)
                            for link in re.findall(r"https?://[^\s]+", texto):
                                url_limpia = link.rstrip(",.;:\"')")
                                candidatos_encontrados.append({
                                    "id": hashlib.sha1((url_limpia + nombre).encode("utf-8")).hexdigest()[:16],
                                    "name": html.unescape(texto[:120]) + "...",
                                    "titulo": html.unescape(texto[:120]),
                                    "brand": "Canal Ofertas",
                                    "url": url_limpia,
                                    "precio_actual": 0.0,  # el motor cruza esto con el historial local
                                    "precio_anterior": None,
                                    "tienda": "WhatsApp_Feed",
                                    "origen_link": "whatsapp",
                                    "tipo_fuente": "WEB_FALLBACK",
                                })
                    except Exception as err:  # un canal roto no detiene a los demás
                        logger.error("[WHATSAPP] Error en canal '%s': %s", nombre, err)
                        estados.append("error")
            finally:
                await context.close()
                await browser.close()

        if estados and not any(e == "ok" for e in estados):
            LAST_STATUS["state"] = next((e for e in estados if e != "ok"), "error")
        else:
            LAST_STATUS["state"] = "ok"
        return candidatos_encontrados


def buscar_whatsapp(contexto=None) -> list:
    """Punto de entrada síncrono compatible con el orquestador."""
    LAST_STATUS.clear()
    scraper = WhatsAppChannelScraper(contexto)
    try:
        return asyncio.run(scraper.buscar_canales_async())
    except ImportError:
        LAST_STATUS["state"] = "not_configured"
        logger.warning("[WHATSAPP] Playwright no está instalado; fuente omitida.")
        return []
    except Exception as e:
        LAST_STATUS["state"] = "error"
        logger.error("[WHATSAPP] Fallo en ejecución: %s: %s", type(e).__name__, e)
        return []
