import html
import re
import time

import requests
from bs4 import BeautifulSoup

from core.ai_reparador import analizar_publicacion, reparar_url
from core.liquidation_engine import detect_priority_brand, infer_category

TELEGRAM_CHANNELS = [
    ("Ofertones México", "https://t.me/s/OfertonesMexico"),
    ("LiquidAhorros", "https://t.me/s/LiquidAhorrosOficial"),
    ("Outlet y Reacondicionados", "https://t.me/s/outletyreacondicionados"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/140 Safari/537.36", "Accept-Language": "es-MX,es;q=0.9"}
PRICE_RE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)")
URL_RE = re.compile(r"https?://[^\s<>\]\)"']+")

def _prices(text):
    out = []
    for value in PRICE_RE.findall(text or ""):
        try:
            n = float(value.replace(",", ""))
            if 1 <= n <= 2_000_000:
                out.append(n)
        except ValueError:
            pass
    return out

def _store(text):
    low = (text or "").lower()
    for name, needles in [
        ("Mercado Libre", ("mercadolibre", "meli.la")), ("Amazon", ("amazon.com.mx", "amzn.to")),
        ("Walmart", ("walmart",)), ("Bodega Aurrera", ("bodegaaurrera",)),
        ("Chedraui", ("chedraui",)), ("Soriana", ("soriana",)), ("Liverpool", ("liverpool",)),
        ("Coppel", ("coppel",)), ("Suburbia", ("suburbia",)), ("Oferstock", ("oferstock",)),
    ]:
        if any(x in low for x in needles):
            return name
    return "Telegram"

def _candidate(source, text):
    urls = [u.rstrip(".,;:!?)]}>\'"") for u in URL_RE.findall(text or "")]
    if not urls:
        return None
    external = [u for u in urls if not u.startswith(("https://t.me/", "https://telegram.me/"))]
    url = external[0] if external else urls[0]
    tienda = _store(text + " " + url)
    url = reparar_url(url, tienda, text)

    prices = _prices(text)
    actual = None
    anterior = None
    for line in text.splitlines():
        vals = _prices(line)
        if not vals:
            continue
        ll = line.lower()
        if any(x in ll for x in ("precio final", "precio oferta", "precio de oferta", "a tan sólo", "a solo", "ahora", "desde")):
            actual = vals[-1]
        if any(x in ll for x in ("precio anterior", "antes", "original")):
            anterior = vals[-1]
    if actual is None and prices:
        actual = prices[0]
    if anterior is None and actual and len(prices) > 1:
        bigger = [p for p in prices if p > actual]
        anterior = min(bigger) if bigger else None

    low = text.lower()
    liquidacion = any(x in low for x in ("liquidación", "liquidacion", "remate", "outlet", "última pieza", "ultima pieza", "saldo", "reacondicionado", "error de precio"))
    ai = analizar_publicacion(text, url, tienda)
    titulo = str(ai.get("titulo") or "").strip()
    marca = str(ai.get("marca") or "").strip()
    categoria = str(ai.get("categoria") or "").strip()
    if isinstance(ai.get("precio_actual"), (int, float)) and ai["precio_actual"] > 0:
        actual = float(ai["precio_actual"])
    if isinstance(ai.get("precio_anterior"), (int, float)) and ai["precio_anterior"] > actual:
        anterior = float(ai["precio_anterior"])
    tienda = str(ai.get("tienda") or tienda)
    if ai.get("url"):
        url = reparar_url(str(ai["url"]), tienda, text)
    liquidacion = liquidacion or bool(ai.get("liquidacion"))
    descuento = round((1 - actual / anterior) * 100) if actual and anterior and anterior > actual else int(ai.get("descuento") or 0)

    if not titulo:
        lines = [re.sub(r"^[👉🔥⚡️➡️⭐️✅🚨🛒💥🎉]+\s*", "", x).strip() for x in text.splitlines() if x.strip()]
        titulo = next((x for x in lines if len(x) > 12 and not x.startswith(("http", "#", "•"))), text[:180])
    if not marca:
        marca, _ = detect_priority_brand({"titulo": titulo})
    if not categoria:
        categoria = infer_category({"titulo": titulo, "marca": marca})
    if not actual or not url:
        return None
    return {
        "tienda": tienda, "titulo": titulo[:180], "marca": marca, "categoria": categoria,
        "precio_actual": actual, "precio_anterior": anterior, "descuento": descuento,
        "url": url, "liquidacion": liquidacion,
        "outlet": any(x in low for x in ("outlet", "reacondicionado", "open box")),
        "origen": source, "origen_link": "Telegram", "publicacion": html.unescape(text[:4000]),
    }

def buscar_telegram(session):
    resultados = []
    for source, base in TELEGRAM_CHANNELS:
        try:
            response = session.get(base, headers=HEADERS, timeout=25)
            if response.status_code >= 400:
                print(f"Telegram/{source}: HTTP {response.status_code}")
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            posts = soup.select(".tgme_widget_message")
            print(f"Telegram/{source}: {len(posts)} publicaciones visibles")
            for post in posts[-80:]:
                node = post.select_one(".tgme_widget_message_text")
                if node:
                    candidate = _candidate(source, node.get_text("\n", strip=True))
                    if candidate:
                        resultados.append(candidate)
            time.sleep(0.5)
        except requests.RequestException as error:
            print(f"Telegram/{source}: error {error}")
        except Exception as error:
            print(f"Telegram/{source}: error procesando publicaciones: {error}")
    return resultados
