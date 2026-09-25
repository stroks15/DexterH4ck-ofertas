import os
import re
import hashlib
import sqlite3
import asyncio
import random
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

DB = "historial.db"

FUENTES = {
    "Amazon MX": "https://www.amazon.com.mx/s?k={q}",
    "Mercado Libre MX": "https://listado.mercadolibre.com.mx/{q}",
    "Walmart MX": "https://www.walmart.com.mx/search?q={q}",
    "Chedraui": "https://www.chedraui.com.mx/search?q={q}",
    "Preciometro": "https://preciometro.com/buscar?q={q}",
}

BUSQUEDAS = [
    "iphone", "laptop", "smart tv", "playstation", "xbox",
    "nintendo switch", "audifonos", "smartwatch", "tenis",
    "refrigerador", "lavadora"
]

KEYWORDS = [
    "remate", "liquidacion", "liquidación", "outlet",
    "caja abierta", "open box", "oferta", "super oferta"
]


def init_db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS productos(id TEXT PRIMARY KEY,titulo TEXT,precio REAL,fecha TEXT)")
    con.commit()
    con.close()


def nuevo_o_mejor(pid, titulo, precio):
    con = sqlite3.connect(DB)
    viejo = con.execute("SELECT precio FROM productos WHERE id=?", (pid,)).fetchone()
    if not viejo or precio < viejo[0]:
        con.execute("INSERT OR REPLACE INTO productos VALUES(?,?,?,?)", (pid, titulo, precio, str(datetime.now())))
        con.commit()
        con.close()
        return True
    con.close()
    return False


def telegram(texto):
    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": texto, "parse_mode": "Markdown"},
            timeout=15,
        )


def extraer_precio(texto):
    precios = re.findall(r"\$\s?([0-9,]+(?:\.\d{2})?)", texto)
    if not precios:
        return None
    return float(precios[0].replace(",", ""))


def es_liquidacion(tienda, texto, precio):
    texto = texto.lower()

    if tienda in ["Walmart MX", "Chedraui"]:
        if re.search(r"\.(01|02|03)$", f"{precio:.2f}"):
            return True

    return any(k in texto for k in KEYWORDS)


async def obtener_html(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightTimeout:
        print("Timeout parcial", url)

    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeout:
        pass

    return await page.locator("body").inner_text(timeout=10000)


async def main():
    init_db()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            locale="es-MX",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140 Safari/537.36",
            viewport={"width":1366,"height":768},
            extra_http_headers={"Accept-Language":"es-MX,es;q=0.9"}
        )

        page = await context.new_page()

        for tienda, plantilla in FUENTES.items():
            for q in BUSQUEDAS:
                try:
                    url = plantilla.format(q=q)
                    texto = await obtener_html(page, url)
                    precio = extraer_precio(texto)

                    if precio:
                        titulo = texto.split("\n")[0][:180]
                        pid = hashlib.sha256(f"{tienda}-{titulo}".encode()).hexdigest()

                        if es_liquidacion(tienda, texto, precio) and nuevo_o_mejor(pid, titulo, precio):
                            telegram(
                                f"*{tienda}*\n\n{titulo}\n\nPrecio detectado: ${precio:,.2f} MXN\n\n{url}"
                            )

                except Exception as e:
                    print(tienda, q, e)

                await asyncio.sleep(random.uniform(3,8))

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
