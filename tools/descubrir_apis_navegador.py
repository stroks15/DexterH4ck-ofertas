# tools/descubrir_apis_navegador.py
import os
import json
import asyncio
import logging
import random
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DexterH4ck.ApiDiscover")

# Pool de User-Agents comerciales actualizados para la inspección
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

async def descubrir_endpoints():
    url_objetivo = os.environ.get("NETWORK_DISCOVERY_URL")
    try:
        wait_ms = int(os.environ.get("NETWORK_DISCOVERY_WAIT_MS", "7000"))
    except (ValueError, TypeError):
        wait_ms = 7000

    if not url_objetivo:
        logger.error("Falta la variable de entorno NETWORK_DISCOVERY_URL. Abortando.")
        return

    logger.info(f"Iniciando inspección camuflada sobre la URL: {url_objetivo}")
    endpoints_capturados = {}

    async with async_playwright() as p:
        # Lanzamiento con desactivación explícita de marcas de automatización (WAF Bypass)
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080"
            ]
        )

        # Configuración de contexto simulando un entorno orgánico interactivo de México
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale="es-MX",
            timezone_id="America/Mexico_City",
            viewport={"width": 1920, "height": 1080}
        )

        page = await context.new_page()

        # Inyección de scripts avanzados para eliminar rastros de automatización en el motor Blink
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['es-MX', 'es', 'en-US', 'en']});
        """)

        # Aplicación nativa de la librería de sigilo
        await stealth_async(page)

        # Interceptor de respuestas de red en tiempo real
        async def interceptar_respuesta(response):
            try:
                content_type = response.headers.get("content-type", "").lower()
                # Captura solo tráfico útil: llamadas REST JSON, APIs internas o GraphQL
                if "application/json" in content_type or "graphql" in response.url:
                    url_completa = response.url
                    parsed_url = urlparse(url_completa)
                    host = parsed_url.netloc
                    
                    if host not in endpoints_capturados:
                        endpoints_capturados[host] = []
                        
                    # Evitamos almacenar duplicados exactos en el reporte final
                    if url_completa not in endpoints_capturados[host]:
                        endpoints_capturados[host].append(url_completa)
                        logger.info(f"[CAPTURA JSON] EndPoint detectado en {host} -> {url_completa[:90]}...")
            except Exception:
                pass

        # Vinculación del evento de escucha de red
        page.on("response", interceptar_response=interceptar_respuesta)

        try:
            # Esperamos a que el DOM esté listo o la red se estabilice
            await page.goto(url_objetivo, wait_until="domcontentloaded", timeout=45000)
            logger.info(f"Página base cargada de forma exitosa. Esperando {wait_ms} ms para capturar peticiones asíncronas...")
            await asyncio.sleep(wait_ms / 1000.0)
        except Exception as e:
            logger.error(f"Error o timeout controlado durante el renderizado dinámico: {str(e)}")
        finally:
            await context.close()
            await browser.close()

    # Escritura defensiva del reporte de diagnóstico
    output_file = "network_endpoints.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(endpoints_capturados, f, ensure_ascii=False, indent=2)
        
    logger.info(f"Análisis perimetral completado. Archivo '{output_file}' generado con {len(endpoints_capturados)} hosts mapeados.")

if __name__ == "__main__":
    asyncio.run(descubrir_endpoints())
