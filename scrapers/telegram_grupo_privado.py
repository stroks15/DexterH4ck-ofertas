"""
Lee mensajes nuevos de un grupo/canal privado de Telegram (uno al que
nuestro propio bot fue agregado como miembro) y reenvía directo los que
traigan un descuento de 50% a 99%.

A diferencia de scrapers/telegram_ofertas.py (que lee canales PÚBLICOS
vía su vista web https://t.me/s/<usuario>), un grupo privado solo se
puede leer si el bot es miembro: no hay vista web pública para un
enlace de invitación (https://t.me/+xxxx). Por eso esto usa la Bot API
(getUpdates) en vez de scraping HTML.

Esta fuente es intencionalmente independiente del resto del pipeline
(core/liquidation_engine.py, validar_y_filtrar_bomba, etc.): el pedido
fue que, para este grupo en particular, el único requisito sea el
porcentaje de descuento que el propio mensaje reporta (50%-99%), sin
exigir precio anterior, enlace de producto validado, ni nada más. Por
eso envía directo en vez de pasar por procesar_candidatos().

Formato de mensaje que entiende (ejemplo real):

    Walmart Oferstock NACIONAL
    -36%

    Labial líquido L'Oréal Infallible Le Matte Resistance ...

    Precio: $190.00
    (Antes: $299.00)
    Vendedor: por Walmart
    Stock: EN STOCK
    SKU: 00007124967483
    ▓▓▓▓░░░░░░ 36%
"""

import json
import os
import re
import time

import requests

MIN_DESCUENTO_GRUPO = 50
MAX_DESCUENTO_GRUPO = 99
OFFSET_FILE = "tg_grupo_offset.json"

GRUPO_FUENTE_CHAT_ID = os.environ.get("GRUPO_FUENTE_CHAT_ID", "").strip()

DESCUENTO_RE = re.compile(r"-(\d{1,3})\s*%")
PRECIO_RE = re.compile(r"Precio:\s*\$\s*([\d,]+\.?\d*)", re.I)
ANTES_RE = re.compile(r"Antes:\s*\$\s*([\d,]+\.?\d*)", re.I)
SKU_RE = re.compile(r"SKU:\s*(\S+)", re.I)
URL_RE = re.compile(r"https?://[^\s<>]+")


def _cargar_offset() -> int:
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE, "r", encoding="utf-8") as fh:
                return int(json.load(fh).get("offset", 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return 0
    return 0


def _guardar_offset(offset: int) -> None:
    temporal = f"{OFFSET_FILE}.tmp"
    with open(temporal, "w", encoding="utf-8") as fh:
        json.dump({"offset": offset}, fh)
    os.replace(temporal, OFFSET_FILE)


def _url_de_botones(mensaje: dict) -> str | None:
    """Muchos bots de ofertas ponen el enlace como botón ('Comprar')
    en vez de en el texto del mensaje. Lo buscamos ahí también."""
    teclado = (mensaje.get("reply_markup") or {}).get("inline_keyboard") or []
    for fila in teclado:
        for boton in fila:
            url = boton.get("url")
            if url and not url.startswith(("https://t.me/", "https://telegram.me/")):
                return url
    return None


def parsear_oferta(texto: str, mensaje: dict | None = None) -> dict | None:
    descuento_match = DESCUENTO_RE.search(texto)
    if not descuento_match:
        return None
    descuento = int(descuento_match.group(1))

    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    tienda = lineas[0] if lineas else "Telegram"

    titulo = None
    for linea in lineas[1:]:
        if linea.lower().startswith("precio:"):
            break
        if re.match(r"^-?\d{1,3}\s*%$", linea):
            continue
        titulo = linea
        break

    precio_m = PRECIO_RE.search(texto)
    antes_m = ANTES_RE.search(texto)
    sku_m = SKU_RE.search(texto)

    url = None
    if mensaje:
        url = _url_de_botones(mensaje)
    if not url:
        urls_texto = [u.rstrip(".,;:!?)]}>'\"") for u in URL_RE.findall(texto)]
        urls_texto = [u for u in urls_texto if not u.startswith(("https://t.me/", "https://telegram.me/"))]
        url = urls_texto[0] if urls_texto else None

    return {
        "descuento": descuento,
        "tienda": tienda,
        "titulo": titulo or "Producto sin título",
        "precio": precio_m.group(1) if precio_m else None,
        "antes": antes_m.group(1) if antes_m else None,
        "sku": sku_m.group(1) if sku_m else None,
        "url": url,
    }


def _formatear_alerta(oferta: dict) -> str:
    lineas = [
        f"🔥 <b>{oferta['descuento']}% de descuento</b> — {oferta['tienda']}",
        "",
        oferta["titulo"],
        "",
    ]
    if oferta["precio"]:
        lineas.append(f"Precio: ${oferta['precio']}")
    if oferta["antes"]:
        lineas.append(f"(Antes: ${oferta['antes']})")
    if oferta["sku"]:
        lineas.append(f"SKU: {oferta['sku']}")
    if oferta["url"]:
        lineas.append(oferta["url"])
    return "\n".join(lineas)


def _enviar(token: str, chat_id_destino: str, texto: str) -> bool:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id_destino,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        if resp.status_code == 429:
            retry_after = int((resp.json().get("parameters") or {}).get("retry_after", 5))
            time.sleep(min(max(retry_after, 1), 30) + 0.5)
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id_destino, "text": texto, "parse_mode": "HTML"},
                timeout=20,
            )
        return resp.status_code == 200
    except requests.RequestException as exc:
        print(f"[grupo_privado] Error enviando a Telegram: {exc}")
        return False


def revisar_grupo_privado(token: str, chat_id_destino: str) -> tuple[int, int]:
    """Devuelve (enviadas, candidatos_con_descuento_valido)."""
    if not GRUPO_FUENTE_CHAT_ID:
        print("[grupo_privado] GRUPO_FUENTE_CHAT_ID no configurado; se omite esta fuente.")
        return 0, 0
    if not token:
        return 0, 0

    offset = _cargar_offset()
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={
                "offset": offset,
                "timeout": 0,
                "allowed_updates": json.dumps(["message", "channel_post"]),
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"[grupo_privado] Error consultando getUpdates: {exc}")
        return 0, 0

    if not data.get("ok", False):
        print(f"[grupo_privado] getUpdates respondió error: {data}")
        return 0, 0

    enviadas = 0
    candidatos = 0
    max_update_id = offset - 1

    for update in data.get("result", []):
        update_id = update.get("update_id", 0)
        max_update_id = max(max_update_id, update_id)

        mensaje = update.get("message") or update.get("channel_post")
        if not mensaje:
            continue

        chat = mensaje.get("chat", {})
        if str(chat.get("id")) != GRUPO_FUENTE_CHAT_ID:
            continue

        texto = mensaje.get("text") or mensaje.get("caption")
        if not texto:
            continue

        oferta = parsear_oferta(texto, mensaje)
        if not oferta:
            continue

        if not (MIN_DESCUENTO_GRUPO <= oferta["descuento"] <= MAX_DESCUENTO_GRUPO):
            continue

        candidatos += 1
        if _enviar(token, chat_id_destino, _formatear_alerta(oferta)):
            enviadas += 1
        else:
            print(f"[grupo_privado] No se pudo enviar la oferta del {oferta['descuento']}%.")

    # El offset se guarda siempre que avancemos, incluso si una oferta
    # puntual falló al enviarse: Telegram no reenvía mensajes viejos y
    # reintentar por siempre el mismo lote bloquearía los nuevos.
    if max_update_id >= offset:
        _guardar_offset(max_update_id + 1)

    return enviadas, candidatos
