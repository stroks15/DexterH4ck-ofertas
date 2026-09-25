import os, re, hashlib, sqlite3, asyncio, random
from datetime import datetime
import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

DB='historial.db'
TIENDAS={'Amazon MX':'https://amazon.com.mx','Mercado Libre MX':'https://mercadolibre.com.mx','Walmart MX':'https://walmart.com.mx','Chedraui':'https://chedraui.com.mx'}
KEYWORDS=['remate','liquidacion','liquidación','outlet','caja abierta','oferta']


def db():
    con=sqlite3.connect(DB)
    con.execute('CREATE TABLE IF NOT EXISTS productos(id TEXT PRIMARY KEY,titulo TEXT,precio REAL,fecha TEXT)')
    con.commit()
    con.close()


def guardar(pid,titulo,precio):
    con=sqlite3.connect(DB)
    viejo=con.execute('SELECT precio FROM productos WHERE id=?',(pid,)).fetchone()
    if not viejo or precio < viejo[0]:
        con.execute('INSERT OR REPLACE INTO productos VALUES(?,?,?,?)',(pid,titulo,precio,str(datetime.now())))
        con.commit(); con.close(); return True
    con.close(); return False


def telegram(texto):
    token=os.getenv('TELEGRAM_TOKEN'); chat=os.getenv('TELEGRAM_CHAT_ID')
    if token and chat:
        requests.post(f'https://api.telegram.org/bot{token}/sendMessage',data={'chat_id':chat,'text':texto,'parse_mode':'Markdown'},timeout=15)


def liquidacion(tienda,titulo,precio):
    t=titulo.lower()
    if tienda in ['Walmart MX','Chedraui']:
        return bool(re.search(r'\.(01|02|03)$',f'{precio:.2f}'))
    return any(x in t for x in KEYWORDS)

async def visitar(page,url):
    try:
        await page.goto(url,wait_until='domcontentloaded',timeout=60000)
    except PlaywrightTimeout:
        print('Carga lenta, usando contenido parcial')
    try:
        await page.wait_for_load_state('networkidle',timeout=10000)
    except PlaywrightTimeout:
        pass
    return await page.locator('body').inner_text(timeout=10000)

async def main():
    db()
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
        context=await browser.new_context(locale='es-MX',viewport={'width':1366,'height':768},user_agent='Mozilla/5.0 Chrome/140')
        page=await context.new_page()
        for tienda,url in TIENDAS.items():
            try:
                texto=await visitar(page,url)
                m=re.search(r'\$\s?([0-9,]+)',texto)
                if m:
                    precio=float(m.group(1).replace(',',''))
                    titulo=texto.split('\n')[0][:150]
                    pid=hashlib.sha256(f'{tienda}-{titulo}'.encode()).hexdigest()
                    if liquidacion(tienda,titulo,precio) and guardar(pid,titulo,precio):
                        telegram(f'*{tienda}*\n{titulo}\n${precio:,.2f}\n{url}')
            except Exception as e:
                print(tienda,e)
            await asyncio.sleep(random.uniform(5,12))
        await browser.close()

if __name__=='__main__': asyncio.run(main())
