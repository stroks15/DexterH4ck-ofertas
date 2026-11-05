"""Telegram MarkdownV2 formatter and delivery helper for deal alerts."""
from __future__ import annotations

import os
import re
import time
from urllib.parse import urlparse

import requests

MARKDOWN_V2_SPECIAL = re.compile(r"([_\*\[\]()~`>#+\-=|{}.!\\])")


def escape_markdown(value) -> str:
    return MARKDOWN_V2_SPECIAL.sub(r"\\\1", str(value or ""))


def clean_product_url(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(ch.isspace() for ch in value) or re.search(r"(?i)(?:null|undefined|\{productid\}|\[object object\])", value):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if any(token in (parsed.path + parsed.query).lower() for token in ("null", "undefined", "{productid}")):
        return None
    return value


def rango_descuento(discount: float) -> tuple[str, str]:
    if 95 <= discount <= 99:
        return "💣", "DIAMANTE"
    if 90 <= discount <= 94:
        return "⚡", "ORO"
    if 70 <= discount <= 89:
        return "🔥", "PLATA"
    if 50 <= discount <= 69:
        return "🏷️", "BRONCE"
    return "🏷️", "OFERTA"


def format_alert(alert: dict) -> str:
    """Build the requested tiered alert using Telegram MarkdownV2 safely."""
    item = alert.get("item") or {}
    discount = int(round(float(alert.get("descuento") or 0)))
    emoji, _ = rango_descuento(discount)
    store = escape_markdown(item.get("tienda") or "Tienda").upper()
    title = escape_markdown(item.get("titulo") or item.get("name") or "Producto")
    brand = escape_markdown(item.get("marca") or item.get("brand") or "Sin especificar")
    category = escape_markdown(item.get("categoria") or item.get("category") or "General")
    current = float(alert.get("actual") or item.get("precio_actual") or 0)
    previous = float(alert.get("referencia") or item.get("precio_anterior") or 0)
    evaluation = alert.get("evaluacion") or {}
    score = int(evaluation.get("puntuacion") or 0)
    verified_discount = (bool(item.get("descuento_verificado")) if "descuento_verificado" in item else previous > current > 0)
    url = clean_product_url(item.get("url"))
    if not url:
        raise ValueError("URL de producto inválida; Telegram no debe publicar enlaces rotos")

    # MarkdownV2 reserves parentheses/backslashes in link targets.
    safe_url = url.replace("\\", "%5C").replace("(", "%28").replace(")", "%29")
    separator = "\\|"
    current_text = escape_markdown(f"${current:,.2f}")
    previous_text = escape_markdown(f"${previous:,.2f}")

    lines = [
        f"{emoji} *¡LIQUIDACIÓN DETECTADA EN {store}!*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📦 *Producto:* {title}",
        f"🏷️ *Marca:* {brand} {separator} 🗂️ *Categoría:* {category}",
    ]
    if previous > current > 0:
        lines.append(f"💰 *Precio Actual:* {current_text} {separator} ❌ *Antes:* {previous_text}")
    else:
        lines.append(f"💰 *Precio Actual:* {current_text}")
    if 50 <= discount <= 99:
        status = "REALES" if verified_discount else "(REPORTADO POR LA FUENTE)"
        lines.append(f"📉 *Descuento:* {discount}% {status}")

    extreme = alert.get("precio_extremo") or {}
    if extreme.get("publicable_como_extremo"):
        level = "ULTRA EXTREMO" if extreme.get("nivel") == "ULTRA_EXTREMO" else "EXTREMO"
        lines.append(f"🛡️ *{level}: precio verificado en la ficha oficial*")

    lines.extend([
        "",
        f"🔗 [Ver Oferta en la Tienda]({safe_url})",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚙️ _Score del Detector: {score} pts_",
    ])
    return "\n".join(lines)


def send_message(text: str, token: str | None = None, chat_id: str | None = None,
                 session: requests.Session | None = None) -> bool:
    token = token or os.getenv("TELEGRAM_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
    client = session or requests.Session()
    for attempt in range(3):
        response = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2", "disable_web_page_preview": False},
            timeout=20,
        )
        if response.ok:
            return True
        if response.status_code == 429:
            try:
                wait = int(response.json().get("parameters", {}).get("retry_after", 5))
            except (ValueError, TypeError):
                wait = 5
            time.sleep(min(max(wait, 1), 120))
            continue
        if response.status_code in (400, 401, 403, 404):
            return False
        time.sleep(min(2 ** attempt, 8))
    return False
