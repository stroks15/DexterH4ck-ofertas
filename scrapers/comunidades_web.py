"""Fuentes web comunitarias públicas de liquidaciones/ofertas en México.

Solo consume páginas públicas. La comunidad es una señal de descubrimiento; la
publicación final exige una URL directa de la tienda y precios coherentes.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DexterH4ck-Ofertas/1.0)",
    "Accept-Language": "es-MX,es;q=0.9",
}
SOURCES = (
    ("LiquidaZona", "https://liquidazona.com/home/"),
    ("Ofertillas", "https://ofertillas.com/"),
)
STORE_HOSTS = {
    "walmart.com.mx": "Walmart MX",
    "bodegaaurrera.com.mx": "Bodega Aurrera",
    "chedraui.com.mx": "Chedraui",
    "soriana.com": "Soriana",
    "liverpool.com.mx": "Liverpool",
    "amazon.com.mx": "Amazon MX",
    "mercadolibre.com.mx": "Mercado Libre MX",
    "coppel.com": "Coppel",
    "suburbia.com.mx": "Suburbia",
    "oferstock.com.mx": "Oferstock",
}
PRICE = re.compile(r"\$\s*([0-9][0-9,.]*)")
CURRENT = re.compile(
    r"(?i)(?:precio\s+(?:actual|final|oferta)|precio\s+de\s+oferta|ahora|precio\s+en\s+rojo)"
    r"\D{0,30}\$?\s*([0-9][0-9,.]*)"
)
REFERENCE = re.compile(
    r"(?i)(?:antes|precio\s+(?:anterior|regular|de\s+lista)|original)"
    r"\D{0,30}\$?\s*([0-9][0-9,.]*)"
)
DISCOUNT = re.compile(r"(?<!\d)([5-9]\d)\s*%\s*(?:de\s*)?(?:descuento|off|rebaja)?", re.I)


def _money(value: str | None) -> float | None:
    try:
        return float(str(value).replace(",", "")) if value else None
    except ValueError:
        return None


def _store_from_url(url: str) -> str | None:
    host = urlparse(url).netloc.lower().split(":")[0]
    for domain, name in STORE_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def _direct_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    found = []
    seen = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(base_url, anchor.get("href", ""))
        store = _store_from_url(href)
        if not store or href in seen:
            continue
        path = urlparse(href).path.lower()
        if any(x in path for x in ("/search", "/buscar", "/ofertas", "/catalogo", "/home")):
            continue
        seen.add(href)
        found.append((store, href))
    return found


def _extract_from_detail(session: requests.Session, source: str, detail_url: str) -> dict | None:
    try:
        response = session.get(detail_url, headers=HEADERS, timeout=15)
        if response.status_code >= 400:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        text = re.sub(r"\s+", " ", html.unescape(soup.get_text(" ", strip=True)))
        current_match = CURRENT.search(text)
        reference_match = REFERENCE.search(text)
        if not current_match or not reference_match:
            return None
        current = _money(current_match.group(1))
        reference = _money(reference_match.group(1))
        if not current or not reference or reference <= current:
            return None
        discount = round((reference - current) / reference * 100)
        if not 40 <= discount <= 99:
            return None
        direct = next(iter(_direct_links(soup, detail_url)), None)
        if not direct:
            return None
        store, product_url = direct
        title = soup.find("h1")
        title_text = title.get_text(" ", strip=True) if title else ""
        return {
            "id": f"{source}:{product_url}",
            "titulo": title_text[:180],
            "nombre": title_text[:180],
            "tienda": store,
            "precio_actual": current,
            "precio_anterior": reference,
            "descuento": discount,
            "url": product_url,
            "origen_link": f"{source.lower()}_comunidad",
            "url_evidencia_comunidad": detail_url,
            "tipo_fuente": "COMUNIDAD",
        }
    except requests.RequestException:
        return None


def buscar_comunidades_web() -> list[dict]:
    session = requests.Session()
    output = []
    seen_details = set()
    for source, home in SOURCES:
        try:
            response = session.get(home, headers=HEADERS, timeout=20)
            if response.status_code >= 400:
                print(f"[COMUNIDAD/{source}] HTTP {response.status_code}")
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            detail_links = []
            for anchor in soup.select("a[href]"):
                href = urljoin(home, anchor.get("href", ""))
                host = urlparse(href).netloc.lower()
                if host.endswith(urlparse(home).netloc.lower()) and href not in seen_details:
                    text = anchor.get_text(" ", strip=True).lower()
                    if any(k in text for k in ("liquid", "oferta", "descuento", "remate", "rebaja")):
                        seen_details.add(href)
                        detail_links.append(href)
            # No más de 15 fichas por fuente para mantener el monitor ligero.
            for detail in detail_links[:15]:
                candidate = _extract_from_detail(session, source, detail)
                if candidate:
                    output.append(candidate)
        except requests.RequestException as error:
            print(f"[COMUNIDAD/{source}] error: {type(error).__name__}")
    print(f"[COMUNIDADES WEB] {len(output)} candidatos con evidencia directa")
    return output
