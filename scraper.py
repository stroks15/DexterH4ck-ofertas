import os, re, json, hashlib, sqlite3, asyncio, random
from datetime import datetime
from playwright.async_api import async_playwright
import requests

DB='historial.db'
TIENDAS={
 'Amazon MX':'https://amazon.com.mx',
 'Mercado Libre MX':'https://mercadolibre.com.mx',
 'Walmart MX':'https://walmart.com.mx',
 'Chedraui':'https://chedraui.com.mx'
}
KEYWORDS=['remate','liquidacion','outlet','caja abierta','oferta']


def db():
 c=sqlite3.connect(DB); c.execute('CREATE TABLE IF NOT EXISTS productos(id TEXT PRIMARY KEY,titulo TEXT,precio REAL,fecha TEXT)'); return c

def nuevo_o_bajo(pid,titulo,precio):
 c=db(); old=c.execute('SELECT precio FROM productos WHERE id=?',(pid,)).fetchone()
 if not old or precio < old[0]:
  c.execute('INSERT OR REPLACE INTO productos VALUES(?,?,?,?)',(pid,titulo,precio,str(datetime.now())))
  c.commit(); return True
 return False


def telegram(msg):
 token=os.getenv('TELEGRAM_TOKEN'); chat=os.getenv('TELEGRAM_CHAT_ID')
 if token and chat:
  requests.post(f'https://api.telegram.org/bot{token}/sendMessage',data={'chat_id':chat,'text':msg})


def liquidacion(tienda,titulo,precio,url):
 txt=titulo.lower();
 if tienda in ['Walmart MX','Chedraui']:
  return bool(re.search(r'\.0[123](\s|$)',str(precio)))
 return any(k in txt for k in KEYWORDS)

async def visitar(page,tienda,url):
 await page.goto(url,wait_until='domcontentloaded',timeout=30000)
 await page.wait_for_load_state('networkidle',timeout=10000)
 texto=await page.locator('body').inner_text()
 return texto[:3000]

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True)
  page=await browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140 Safari/537.36',locale='es-MX',viewport={'width':1366,'height':768})
  for tienda,url in TIENDAS.items():
   try:
    texto=await visitar(page,tienda,url)
    precio=re.search(r'\$\s?([0-9,]+)',texto)
    if precio:
     pval=float(precio.group(1).replace(',',''))
     titulo=texto.split('\n')[0]
     pid=hashlib.sha256(titulo.encode()).hexdigest()
     if liquidacion(tienda,titulo,pval) and nuevo_o_bajo(pid,titulo,pval):
      telegram(f'*{tienda}*\n\n{titulo}\nPrecio: ${pval}\n{url}')
   except Exception as e: print(tienda,e)
   await asyncio.sleep(random.uniform(3,8))
  await browser.close()

if __name__=='__main__': asyncio.run(main())
