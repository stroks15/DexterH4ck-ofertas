import os
import re
import sqlite3
import hashlib
import asyncio
import random
from datetime import datetime
import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

DB = "historial.db"

BUSQUEDAS = ["iphone", "laptop", "smart tv", "playstation", "xbox", "nintendo switch", "audifonos", "smartwatch", "tenis", "refrigerador", "lavadora"]

TIENDAS = {
    "Amazon MX": "https://www.amazon.com.mx/s?k={q}",
    "Mercado Libre MX": "https://listado.mercadolibre.com.mx/{q}",
    "Walmart MX": "https://www.walmart.com.mx/search?q={q}",
    "Chedraui": "https://www.chedraui.com.mx/search?q={q}",
    "Preciometro": "https://preciometro.com/buscar?q={q}"
}

KEYWORDS = ["remate", "liquidacion", "liquidación", "outlet", "caja abierta", "open box", "oferta", "descuento"]


def db_init():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS productos(id TEXT PRIMARY KEY,titulo TEXT,precio REAL,fecha TEXT)")
    con.commit(); con.close()


def es_nuevo(pid, titulo, precio):
    con = sqlite3.connect(DB)
    old = con.execute("SELECT precio FROM productos WHERE id=?", (pid,)).fetchone()
    ok = not old or precio < old[0]
    if ok:
        con.execute("INSERT OR REPLACE INTO productos VALUES(?,?,?,?)", (pid,titulo,precio,str(datetime.now())))
        con.commit()
    con.close()
    return ok


def precio(texto):
    m = re.search(r"\$\s?([0-9,]+(?:\.[0-9]{2})?)", texto)
    return float(m.group(1).replace(',','')) if m else None


def liquidacion(tienda,texto,p):
    t=texto.lower()
    if tienda in ["Walmart MX","Chedraui"]:
        # conserva terminaciones de liquidacion
        if re.search(r"\.(01|02|03)\b", texto):
            return True
    return any(x in t for x in KEYWORDS)


def enviar(msg):
    token=os.getenv('TELEGRAM_TOKEN'); chat=os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",data={"chat_id":chat,"text":msg,"parse_mode":"Markdown"},timeout=20)


async def leer(page,url):
    try:
        await page.goto(url,wait_until='domcontentloaded',timeout=60000)
    except PlaywrightTimeout:
        print('Carga lenta:',url)
    try:
        await page.wait_for_load_state('networkidle',timeout=8000)
    except PlaywrightTimeout:
        pass
    try:
        return await page.locator('body').inner_text(timeout=15000)
    except Exception:
        return ''


async def main():
    db_init()
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
        context=await browser.new_context(locale='es-MX',user_agent='Mozilla/5.0 Chrome/140 Safari/537.36',viewport={'width':1366,'height':768})
        page=await context.new_page()
        for tienda,base in TIENDAS.items():
            encontrados=0
            for q in BUSQUEDAS:
                try:
                    url=base.format(q=q)
                    texto=await leer(page,url)
                    p_actual=precio(texto)
                    if p_actual and liquidacion(tienda,texto,p_actual):
                        titulo=texto.split('\n')[0][:180]
                        pid=hashlib.sha256(f'{tienda}-{titulo}'.encode()).hexdigest()
                        if es_nuevo(pid,titulo,p_actual):
                            enviar(f'*{tienda}*\n\n{titulo}\n\nPrecio: ${p_actual:,.2f} MXN\n\n{url}')
                            encontrados+=1
                except Exception as e:
                    print(tienda,q,e)
                await asyncio.sleep(random.uniform(3,7))
            print(tienda,'alertas:',encontrados)
        await browser.close()

if __name__=='__main__':
    asyncio.run(main())
