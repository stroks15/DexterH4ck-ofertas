import os
import re
import hashlib
import sqlite3
import asyncio
import random
from datetime import datetime

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

DB = "historial.db"

TIENDAS = {
    "Amazon MX": "https://amazon.com.mx",
    "Mercado Libre MX": "https://mercadolibre.com.mx",
    "Walmart MX": "https://walmart.com.mx",
    "Chedraui": "https://chedraui.com.mx",
}

KEYWORDS = ["remate", "liquidacion", "liquidación", "outlet", "caja abierta", "oferta"]


def db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS productos(id TEXT PRIMARY KEY,titulo TEXT,precio REAL,fecha TEXT)")
    con.commit()
    return con


def nuevo_o_bajo(pid, titulo, precio):
    con = db()
    old = con.execute("SELECT precio FROM productos WHERE id=?", (pid,)).fetchone()
    if not old or precio < old[0]:
        con.execute(
            "INSERT OR REPLACE INTO productos VALUES(?,?,?,?)",
            (pid, titulo, precio, str(datetime.now())),
        )
        con.commit()
        return True
    return False


def telegram(msg):
    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": msg, "parse_mode": "Markdown"},
            timeout=15,
        )


def liquidacion(tienda, titulo, precio):
    txt = titulo.lower()
    if tienda in ["Walmart MX", "Chedraui"]:
        return bool(re.search(r"\.0[123]$", f"{precio:.2f}"))
    return any(k in txt for k in KEYWORDS)


async def visitar(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightTimeout:
        print(f"Timeout de carga en {url}, continuando con contenido parcial")

    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeout:
        pass

    return await page.locator("body").inner_text(timeout=10000)


async def main():
    db()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            locale="es-MX",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={"Accept-Language": "es-MX,es;q=0.9"},
        )

        page = await context.new_page()

        for tienda, url in TIENDAS.items():
            try:
                texto = await visitar(page, url)
                precio = re.search(r"\$\s?([0-9,]+(?:\.\d{2})?)", texto)

                if precio:
                    valor = float(precio.group(1).replace(",", ""))
                    titulo = texto.split("\n")[0][:200]
                    pid = hashlib.sha256(f"{tienda}-{titulo}".encode()).hexdigest()

                    if liquidacion(tienda, titulo, valor) and nuevo_o_bajo(pid, titulo, valor):
                        telegram(
                            f"*{tienda}*\n\n{titulo}\n\nPrecio liquidación: ${valor:,.2f} MXN\n\n{url}"
                        )
            except Exception as e:
                print(tienda, e)

            await asyncio.sleep(random.uniform(5, 12))

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
