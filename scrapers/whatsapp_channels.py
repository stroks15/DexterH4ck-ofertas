# scrapers/whatsapp_channels.py
import os
import re
import json
import logging
import random
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

logger = logging.getLogger("DexterH4ck.WhatsApp")

USER_AGENTS_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

class WhatsAppChannelScraper:
    def __init__(self, context=None):
        self.context = context
        # Ruta al archivo config JSON que contiene tus 4 canales objetivo
        self.config_path = "config/fuentes_liquidaciones.json"

    def _cargar_canales(self) -> list:
        if not os.path.exists(self.config_path):
            logger.error(f"[WHATSAPP] Archivo de configuración ausente en: {self.config_path}")
            return []
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("fuentes", [])
        except Exception as e:
            logger.error(f"[WHATSAPP] Error al leer fuentes JSON: {str(e)}")
            return []

    async def buscar_canales_async(self) -> list:
        """
        Levanta una sesión persistente con camuflaje completo para leer
        las vistas previas web de los canales públicos de WhatsApp.
        """
        canales = self._cargar_canales()
        candidatos_encontrados = []

        if not canales:
            return candidatos_encontrados

        # Filtrar únicamente las fuentes configuradas como canales de WhatsApp
        canales_filtrados = [c for i in canales if (c := i) and str(i.get("tipo", "")).lower() == "whatsapp_channel"]

        if not canales_filtrados:
            return candidatos_encontrados

        try:
            wait_ms = int(getattr(self.context, "NETWORK_BROWSER_WAIT_MS", 7000))
        except (ValueError, TypeError):
            wait_ms = 7000

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars"
                ]
            )
            
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS_POOL),
                locale="es-MX",
                timezone_id="America/Mexico_City",
                viewport={"width": 1280, "height": 720}
            )
            
            page = await context.new_page()
            await stealth_async(page)
            
            # Remoción de firmas en runtime
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

            for canal in canales_filtrados:
                nombre = canal.get("nombre", "Canal Anónimo")
                url = canal.get("url")
                
                if not url:
                    continue
                    
                try:
                    logger.info(f"[WHATSAPP] Extrayendo historial dinámico del canal: '{nombre}'")
                    await page.goto(url, wait_until="networkidle", timeout=35000)
                    await asyncio.sleep(wait_ms / 1000.0)
                    
                    html_source = await page.content()
                    soup = BeautifulSoup(html_source, "html.parser")
                    
                    # Selectores del feed de mensajes de WhatsApp en su versión web pública
                    bloques_mensajes = soup.select("div dir='ltr', .whatsapp-public-message-text")
                    
                    for bloque in bloques_mensajes:
                        texto = bloque.text.strip()
                        # Extraemos URLs limpias dentro del mensaje del canal
                        enlaces = re.findall(r'https?://[^\s]+', texto)
                        
                        if enlaces:
                            for link in enlaces:
                                # Limpiamos caracteres residuales del regex en la URL
                                url_limpia = link.rstrip(",. text;\"')")
                                candidatos_encontrados.append({
                                    "id": str(hash(url_limpia + nombre)),
                                    "name": html.unescape(texto[:120]).replace("\n", " ") + "...",
                                    "brand": "Canal Ofertas",
                                    "url": url_limpia,
                                    "precio_actual": 0.0,  # El motor cruzará esto con el historial local
                                    "precio_anterior": None,
                                    "tienda": "WhatsApp_Feed",
                                    "origen_link": "whatsapp",
                                    "tipo_fuente": "WEB_FALLBACK"
                                })
                                
                except Exception as err:
                    logger.error(f"[WHATSAPP] Error en canal '{nombre}': {str(err)}")
                    
            await context.close()
            await browser.close()

        return candidatos_encontrados

def buscar_whatsapp(contexto=None) -> list:
    """Punto de entrada síncrono compatible con el ThreadPoolExecutor del orquestador."""
    scraper = WhatsAppChannelScraper(contexto)
    try:
        # Se ejecuta el loop asíncrono de Playwright dentro del entorno del hilo aislado
        return asyncio.run(scraper.buscar_canales_async())
    except Exception as e:
        logging.getLogger("DexterH4ck.WhatsApp").error(f"Fallo en ejecución síncrona: {str(e)}")
        return []
