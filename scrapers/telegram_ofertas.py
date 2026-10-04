import html
import re
import time
import os

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
MAX_TELEGRAM_AI = int(os.environ.get("GEMINI_MAX_TELEGRAM_AI", "10"))
GEMINI_DELAY = float(os.environ.get("GEMINI_TELEGRAM_DELAY", "1.5"))
_ai_calls = 0
_last_ai_call = 0.0
PRICE_RE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)")
URL_RE = re.compile(r"https?://[^\s<>]+")



def limpiar_titulo_producto(titulo, url=""):
    """Limpia títulos contaminados por precios/metadatos de tarjetas de tienda.
    Nunca cambia los precios estructurados del producto; solo el texto mostrado.
    """
    from urllib.parse import unquote
    texto = html.unescape(str(titulo or ""))
    texto = re.sub(r"\\s+", " ", texto).strip(" \\t\\r\\n-–—|·")
    # Cortar todo lo que claramente pertenece al bloque comercial de precios.
    texto = re.split(
        r"\\b(?:precio\\s+(?:actual|final|de\\s+oferta)|antes|ahorra|hasta\\s+\\d+\\s+mensualidades?|mensualidades?\\s+fijas?|precio\\s+anterior|precio\\s+regular)\\b",
        texto,
        maxsplit=1,
        flags=re.I,
    )[0]
    # Eliminar importes que hayan quedado al principio o al final.
    texto = re.sub(r"(?:^|[|·–—-])\\s*\\$\\s*[0-9][0-9,]*(?:\\s+[0-9]{2})?(?:\\.[0-9]{1,2})?", " ", texto)
    texto = re.sub(r"\\$\\s*[0-9][0-9,]*(?:\\s+[0-9]{2})?(?:\\.[0-9]{1,2})?", " ", texto)
    texto = re.sub(r"\\s{2,}", " ", texto).strip(" \\t\\r\\n-–—|·,;:")
    # Si no quedó un nombre razonable, intenta usar el slug de la ficha directa.
    letras = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü]{2,}", texto)
    if len("".join(letras)) < 5 and url:
        try:
            path = unquote(urlparse(url).path).rstrip("/")
            slug = path.rsplit("/", 1)[-1]
            slug = re.sub(r"(?:-)?(?:mlm[-_]?)?\\d{5,}$", "", slug, flags=re.I)
            slug = re.sub(r"[-_]+", " ", slug)
            slug = re.sub(r"\\b(?:ip|pdp|producto|product|item)\\b", " ", slug, flags=re.I)
            slug = re.sub(r"\\s{2,}", " ", slug).strip(" -_/")
            if len(slug) >= 5:
                texto = slug
        except Exception:
            pass
    return texto[:180]
\n\ndef _prices(text):
    out = []
    for value in PRICE_RE.findall(text or ""):
        try:
            n = float(value.replace(",", ""))
            if 1 <= n <= 2_000_000:
                out.append(n)
        except ValueError:
            pass
    return out

def urlparse_safe(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""

def _condiciones(text):
    low = (text or "").lower()
    condiciones = []
    if "planea y ahorra" in low or "subscribe & save" in low:
        condiciones.append("Planea y Ahorra")
    if "cupón" in low or "cupon" in low or "coupon" in low:
        condiciones.append("cupón")
    if "seguidor de la tienda" in low or "seguir la tienda" in low:
        condiciones.append("seguir la tienda")
    if "compra mínima" in low or "mínimo de compra" in low or "minima de compra" in low:
        condiciones.append("compra mínima")
    if "comprando" in low or "compra 5" in low or "compra 10" in low or "por volumen" in low:
        condiciones.append("cantidad/volumen")
    if "prime" in low:
        condiciones.append("Amazon Prime")
    return condiciones

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

def _esperar_gemini():
    global _last_ai_call
    ahora = time.monotonic()
    restante = GEMINI_DELAY - (ahora - _last_ai_call)
    if restante > 0:
        time.sleep(restante)
    _last_ai_call = time.monotonic()

def _candidate(source, text, session=None):
    urls = [u.rstrip(".,;:!?)]}>'\"") for u in URL_RE.findall(text or "")]
    if not urls:
        return None
    external = [u for u in urls if not u.startswith(("https://t.me/", "https://telegram.me/"))]
    url = external[0] if external else urls[0]
    tienda = _store(text + " " + url)
    url = reparar_url(url, tienda, text)
    if session and urlparse_safe(url) in ("amzn.to", "meli.la", "mercadolibre.com", "mercadolibre.com", "bit.ly", "tidd.ly", "link.amazon"):
        try:
            r = session.get(url, headers=HEADERS, allow_redirects=True, timeout=12)
            if r.url and not r.url.startswith(("https://t.me/", "https://telegram.me/")):
                url = reparar_url(r.url, tienda, text)
        except requests.RequestException:
            pass
    if urlparse_safe(url) in ("t.me", "telegram.me"):
        return None

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
    global _ai_calls
    _esperar_gemini()
    ai = analizar_publicacion(text, url, tienda) if _ai_calls < MAX_TELEGRAM_AI else {}
    if ai:
        _ai_calls += 1
    titulo = limpiar_titulo_producto(str(ai.get("titulo") or "").strip(), url)
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
    condiciones = _condiciones(text)

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
        "condiciones": condiciones,
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
            for post in posts[-60:]:
                node = post.select_one(".tgme_widget_message_text")
                if node:
                    candidate = _candidate(source, node.get_text("\n", strip=True), session)
                    if candidate:
                        resultados.append(candidate)
            time.sleep(0.5)
        except requests.RequestException as error:
            print(f"Telegram/{source}: error {error}")
        except Exception as error:
            print(f"Telegram/{source}: error procesando publicaciones: {error}")
    return resultados
