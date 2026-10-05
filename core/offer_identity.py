"""Identidad y deduplicación transversal de ofertas.

Una misma oferta puede llegar por API, scraper oficial, RSS, Telegram o
comunidad con IDs y URLs diferentes. Este módulo genera identidades estables
sin usar el precio como parte de la identidad.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "tag", "linkcode", "ref", "ref_", "psc", "smid", "pd_rd_i", "pd_rd_r",
    "pd_rd_w", "pd_rd_wg", "pf_rd_p", "pf_rd_r", "camp", "creative",
    "creativeasin", "ascsubtag", "affid", "affiliate", "aff", "irclickid",
}

STORE_ALIASES = {
    "walmart": "walmart",
    "walmart mx": "walmart",
    "bodega aurrera": "bodega",
    "chedraui": "chedraui",
    "soriana": "soriana",
    "liverpool": "liverpool",
    "amazon": "amazon",
    "amazon mx": "amazon",
    "mercado libre": "mercadolibre",
    "mercadolibre": "mercadolibre",
    "mercado libre mx": "mercadolibre",
    "coppel": "coppel",
    "suburbia": "suburbia",
    "oferstock": "oferstock",
}


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def canonical_store(value: object) -> str:
    raw = normalize_text(value)
    for alias, store in STORE_ALIASES.items():
        if raw == normalize_text(alias) or normalize_text(alias) in raw:
            return store
    return raw or "desconocida"


def canonical_url(url: object) -> str:
    try:
        parsed = urlparse(str(url or "").strip())
        if not parsed.netloc:
            return ""
        query = [
            (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
        ]
        path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
        return urlunparse((parsed.scheme.lower() or "https", parsed.netloc.lower(), path, "", urlencode(query), ""))
    except Exception:
        return str(url or "").strip()


def product_code(url: object) -> str:
    value = canonical_url(url)
    if not value:
        return ""
    path = urlparse(value).path

    patterns = (
        r"/dp/([A-Z0-9]{10})(?:/|$)",                    # Amazon
        r"/gp/product/([A-Z0-9]{10})(?:/|$)",            # Amazon
        r"/tienda/pdp/[^/]+/([0-9]{6,})(?:/|$)",         # Liverpool
        r"/pdp/[^/]+/([0-9]{6,})(?:/|$)",                # Liverpool/Coppel variants
        r"/ip/[^/]+/([0-9]{6,})(?:/|$)",                 # Walmart/Bodega
        r"/mlm-([0-9]+)(?:/|$)",                         # Mercado Libre
        r"/p/[^/]+/([0-9]{6,})(?:/|$)",                  # Chedraui/Soriana/etc.
    )
    for pattern in patterns:
        match = re.search(pattern, path, re.I)
        if match:
            return match.group(1).lower()
    # IDs numéricos largos al final de una ficha.
    match = re.search(r"(?:^|/)([0-9]{7,})(?:/|$)", path)
    return match.group(1).lower() if match else ""


def normalized_title(item: dict) -> str:
    title = item.get("titulo") or item.get("title") or item.get("nombre") or ""
    title = normalize_text(title)
    # Quitar ruido frecuente de publicaciones, sin quitar el modelo/número.
    title = re.sub(
        r"\b(?:oferta|oferton|ofertaza|liquidacion|liquidación|remate|outlet|"
        r"descuento|precio oferta|precio final|antes|ahorra|nuevo precio)\b",
        " ",
        title,
    )
    return re.sub(r"\s+", " ", title).strip()


def identity_keys(item: dict) -> set[str]:
    store = canonical_store(item.get("tienda") or item.get("store"))
    url = canonical_url(item.get("url"))
    code = product_code(url)
    title = normalized_title(item)
    keys: set[str] = set()
    if code:
        keys.add(f"{store}|code:{code}")
    if title:
        keys.add(f"{store}|title:{title}")
    if url:
        keys.add(f"{store}|url:{url}")
    return keys


def history_key(item: dict) -> str:
    keys = identity_keys(item)
    # Prefer code, luego título, luego URL para que el registro sea estable.
    return sorted(keys, key=lambda key: (0 if "|code:" in key else 1 if "|title:" in key else 2, key))[0] if keys else ""


def candidate_quality(item: dict) -> tuple:
    """Ordena fuentes: ficha oficial/API > comunidad, y datos completos > incompletos."""
    origin = str(item.get("origen_link") or item.get("origen") or "").lower()
    source_rank = 0
    if "api" in origin or "graphql" in origin or "vtex" in origin:
        source_rank = 40
    elif "oficial" in origin or "buscador" in origin:
        source_rank = 30
    elif "feed" in origin or "promodescuentos" in origin:
        source_rank = 20
    elif "telegram" in origin:
        source_rank = 10
    has_reference = bool(item.get("precio_anterior") or item.get("previous_price"))
    direct = bool(item.get("url"))
    discount = float(item.get("descuento") or 0)
    return (
        source_rank,
        20 if direct else 0,
        10 if has_reference else 0,
        discount,
    )


def deduplicate_candidates(items: list[dict]) -> tuple[list[dict], int]:
    """Deduplica en un ciclo completo sin depender del orden de los hilos."""
    best: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    duplicates = 0

    for item in sorted(items, key=candidate_quality, reverse=True):
        keys = identity_keys(item)
        if not keys:
            continue
        existing_id = next((aliases[k] for k in keys if k in aliases), None)
        canonical = existing_id or min(keys)
        if canonical in best:
            duplicates += 1
            continue
        best[canonical] = item
        for key in keys:
            aliases[key] = canonical

    return list(best.values()), duplicates
