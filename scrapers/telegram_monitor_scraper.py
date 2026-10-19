"""Adaptador unificado de canales de Telegram para el monitor de ofertas.

Dos lectores independientes (cada uno es un worker aislado en ``monitor_ofertas.py``; un
retraso de red o de la sesión de Telegram no frena a los scrapers de tiendas):

* ``buscar_telegram_publico``  Canales públicos leídos desde la vista web ``https://t.me/s/<canal>``.
  No requiere credenciales. Respeta ``SourceCircuit``: ante 401/403/404/451 no reintenta.
* ``buscar_telegram_privado``  Grupo privado (p. ej. "Walmart Channel") leído con Telethon
  usando una cuenta de usuario ya unida al grupo. Solo variables de entorno (GitHub Secrets):

      TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING   (obligatorias)
      TELEGRAM_WALMART_INVITE   enlace o hash de invitación (opcional; NO va en el repo público)
      TELEGRAM_WALMART_CHAT_ID  id numérico del chat (opcional)
      TELEGRAM_AUTO_JOIN=1      unirse con la invitación si la cuenta aún no es miembro (opcional)

  La cuenta lee mensajes como cualquier cliente de Telegram: no publica, no reenvía y no
  intenta evadir límites; ante ``FloodWait`` largo se detiene y devuelve lo leído.

Qué extrae: solo URLs de tiendas objetivo (Walmart, Bodega Aurrera, Chedraui, Soriana,
Mercado Libre, Amazon, Coppel, Suburbia, Liverpool), incluyendo enlaces ocultos en texto,
botones y acortadores (amzn.to, meli.la...). Cada precio pasa por ``core.price_sanitizer``
(un gramaje como "875g" o "1 kg" jamás actúa como precio de referencia).

Reglas de negocio: solo se emiten ofertas con descuento entre 50 % y 99 %, calculado con el
precio anterior o, si el mensaje no lo trae, el porcentaje que el propio mensaje declara
(``descuento_declarado``). Sin ningún descuento legible el monitor puede usar el historial. Las de 95-99 % salen marcadas con
``bomba_candidata``; la regla "una sola vez" (historial local + Supabase), la validación del
enlace y el 💣 al inicio del mensaje las aplica ``monitor_ofertas.procesar_candidatos`` /
``formatear_alerta`` sobre cualquier fuente, de modo que este módulo no puede saltárselas.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from core.discount_text import descuento_declarado
from core.liquidation_engine import detect_priority_brand, infer_category
from core.price_sanitizer import parse_price, sanear_par_precios
from core.source_resilience import HONEST_USER_AGENT, SourceCircuit, classify_status
from scrapers.telegram_ofertas import _condiciones, limpiar_titulo_producto

logger = logging.getLogger("DexterH4ck.TelegramMonitor")


def _env_num(nombre: str, defecto, tipo=float):
    try:
        return tipo((os.environ.get(nombre) or "").strip() or defecto)
    except ValueError:
        return tipo(defecto)


FUENTES_JSON = os.environ.get("TELEGRAM_FUENTES_JSON", "fuentes_liquidaciones.json")
MIN_DESCUENTO, MAX_DESCUENTO = 50, 99
BOMBA_MIN, BOMBA_MAX = 95, 99
MAX_EDAD_HORAS = _env_num("TELEGRAM_MAX_EDAD_HORAS", 48.0)
MAX_POSTS_POR_CANAL = _env_num("TELEGRAM_MAX_POSTS", 60, int)
MAX_MENSAJES_PRIVADO = _env_num("TELEGRAM_MAX_MENSAJES_PRIVADO", 150, int)
PRIVADO_TIMEOUT = _env_num("TELEGRAM_PRIVADO_TIMEOUT", 75.0)
MAX_RESOLUCIONES = _env_num("TELEGRAM_MAX_RESOLUCIONES", 30, int)

# Estado de cada worker; el orquestador lo lee con _lazy_attr (un dict por worker: sin carreras).
STATUS_PUBLICO: dict = {"state": "not_run"}
STATUS_PRIVADO: dict = {"state": "not_run"}

# Si el JSON no existe se usan estas fuentes (el JSON manda cuando existe).
FUENTES_POR_DEFECTO = (
    {"nombre": "Grupo Walmart Channel", "modo": "telethon", "prioridad": 10, "titulo_hint": "walmart"},
    {"nombre": "LiquidaZona Oficial", "modo": "web", "handle": "LiquidaZonaOficial", "prioridad": 5},
    {"nombre": "Ofertas Perronas MX", "modo": "web", "handle": "ofertasperronasmx", "prioridad": 5},
    {"nombre": "Ofertones México", "modo": "web", "handle": "OfertonesMexico", "prioridad": 5},
)

# ---------------------------------------------------------------------------
# Tiendas y enlaces
# ---------------------------------------------------------------------------
_TIENDAS_POR_HOST = (
    ("bodegaaurrera.com.mx", "Bodega Aurrera"),
    ("walmart.com.mx", "Walmart MX"),
    ("chedraui.com.mx", "Chedraui"),
    ("soriana.com", "Soriana"),
    ("mercadolibre.com.mx", "Mercado Libre MX"),
    ("amazon.com.mx", "Amazon MX"),
    ("coppel.com", "Coppel"),
    ("suburbia.com.mx", "Suburbia"),
    ("liverpool.com.mx", "Liverpool"),
)
_ACORTADORES = frozenset(
    {"amzn.to", "a.co", "meli.la", "mercadolibre.com", "bit.ly", "tinyurl.com", "cutt.ly", "t.ly", "is.gd", "rb.gy", "ow.ly"}
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_NULOS_RE = re.compile(r"(?<![a-z0-9])(?:null|undefined)(?![a-z0-9])", re.I)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _limpiar_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,;:!?)]}>'\"*_»")


def tienda_de_url(url: str) -> str | None:
    host = _host(url)
    for sufijo, nombre in _TIENDAS_POR_HOST:
        if host == sufijo or host.endswith("." + sufijo):
            return nombre
    return None


def url_segura(url: str) -> bool:
    """Enlace 100 % utilizable: esquema http(s), host real y sin null/undefined."""
    if not url or any(ch.isspace() for ch in url):
        return False
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname or "." not in p.hostname:
        return False
    return not _NULOS_RE.search(f"{p.path}?{p.query}")


def _crear_resolvedor(sesion: requests.Session, presupuesto: int | None = None):
    """Resuelve acortadores (amzn.to, meli.la...) siguiendo redirecciones, con tope por ciclo."""
    tope = MAX_RESOLUCIONES if presupuesto is None else presupuesto
    cache: dict[str, str] = {}
    usados = [0]

    def resolver(url: str) -> str:
        if _host(url) not in _ACORTADORES:
            return url
        if url in cache:
            return cache[url]
        if usados[0] >= tope:
            return url
        usados[0] += 1
        try:
            with sesion.get(url, allow_redirects=True, timeout=(5, 12), stream=True) as r:
                final = str(r.url or url)
        except requests.RequestException:
            final = url
        cache[url] = final
        return final

    return resolver


def urls_de_tiendas(texto: str, enlaces=(), resolver=None) -> list[tuple[str, str, str]]:
    """[(url_final, tienda, url_cruda)] de tiendas objetivo, sin duplicados."""
    vistos, finales, salida = set(), set(), []
    crudas = [_limpiar_url(u) for u in _URL_RE.findall(texto or "")] + [_limpiar_url(u) for u in (enlaces or ())]
    for cruda in crudas:
        if not cruda or cruda in vistos:
            continue
        vistos.add(cruda)
        url = resolver(cruda) if resolver else cruda
        tienda = tienda_de_url(url)
        if not tienda or not url_segura(url) or urlparse(url).path in ("", "/") or url in finales:
            continue
        finales.add(url)
        salida.append((url, tienda, cruda))
    return salida


# ---------------------------------------------------------------------------
# Precios y título
# ---------------------------------------------------------------------------
_PRECIO_RE = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
_CLAVE_RE = re.compile(r"\b(antes|anterior|original|normal|regular|lista|era|pvp|ahora|oferta|final|solo|sólo|hoy)\b", re.I)
_MODO_ANTERIOR = {"antes", "anterior", "original", "normal", "regular", "lista", "era", "pvp"}
_LINEA_DE_PRECIO = re.compile(r"^(antes|anterior|ahora|precio|normal|regular|descuento|cup[oó]n)\b", re.I)
_EMOJI_INICIO = re.compile(r"^[^\w$¿¡]+")
_LIQUIDACION = (
    "liquidación", "liquidacion", "remate", "outlet", "última pieza", "ultima pieza",
    "saldo", "reacondicionado", "error de precio",
)


def _en_tachado(valor: str, tachados: str) -> bool:
    return bool(tachados) and bool(re.search(rf"(?<![\d,.]){re.escape(valor)}(?!\d)", tachados))


def extraer_precios(segmento: str, tachados=()) -> tuple[float | None, float | None]:
    """(actual, anterior) leyendo palabras clave por línea y texto tachado. Solo montos con ``$``."""
    tachados_txt = " ".join(tachados or ())
    actuales, anteriores, neutros = [], [], []
    for linea in (segmento or "").splitlines():
        eventos = [(m.start(), "k", m.group(1).lower()) for m in _CLAVE_RE.finditer(linea)]
        eventos += [(m.start(), "p", m.group(1)) for m in _PRECIO_RE.finditer(linea)]
        modo = None
        for _, tipo, valor in sorted(eventos):
            if tipo == "k":
                modo = "anterior" if valor in _MODO_ANTERIOR else "actual"
                continue
            precio = parse_price("$" + valor)
            if precio is None:
                continue
            if _en_tachado(valor, tachados_txt) or modo == "anterior":
                anteriores.append(precio)
            elif modo == "actual":
                actuales.append(precio)
            else:
                neutros.append(precio)
    actual = actuales[-1] if actuales else (neutros[0] if neutros else None)
    if actual is None:
        return None, None
    mayores = [p for p in anteriores if p > actual] or [p for p in neutros if p > actual]
    return actual, (min(mayores) if mayores else None)


def _titulo(segmento: str, url: str) -> str:
    for linea in (segmento or "").splitlines():
        limpia = _EMOJI_INICIO.sub("", linea).strip()
        if len(limpia) < 12 or _URL_RE.search(limpia) or not re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]{3,}", limpia):
            continue
        if _LINEA_DE_PRECIO.match(limpia):
            continue
        if _PRECIO_RE.search(limpia) and len(_PRECIO_RE.sub("", limpia).strip()) < 12:
            continue
        titulo = limpiar_titulo_producto(limpia, url)
        if len(titulo) >= 5:
            return titulo
    return limpiar_titulo_producto("", url)


# ---------------------------------------------------------------------------
# Mensaje -> candidatos
# ---------------------------------------------------------------------------
def _segmentos(texto: str, urls: list) -> list[tuple[str | None, str]]:
    """Por cada URL: (texto previo hasta la URL, texto posterior hasta la siguiente URL)."""
    if len(urls) <= 1:
        return [(texto, "")] * len(urls)
    posiciones = [texto.find(cruda) for _, _, cruda in urls]
    if any(p < 0 for p in posiciones):  # enlaces ocultos: solo es seguro atribuir el texto a la primera URL
        return [(texto, "")] + [(None, "")] * (len(urls) - 1)
    segmentos, previo = [], 0
    for i, (_, _, cruda) in enumerate(urls):
        fin = posiciones[i] + len(cruda)
        siguiente = posiciones[i + 1] if i + 1 < len(urls) else len(texto)
        segmentos.append((texto[previo:fin], texto[fin:siguiente]))
        previo = fin
    return segmentos


def _armar(seg, url, tienda, origen, prioridad, tachados, origen_url):
    actual, anterior = extraer_precios(seg, tachados)
    if actual is None:
        return None
    actual, anterior, motivo = sanear_par_precios(actual, anterior, seg)
    if actual is None:
        return None
    if motivo:
        logger.debug("[%s] referencia descartada (%s): %s", origen, motivo, url)
    descuento = 0
    declarado = None
    if anterior:
        descuento = round((1 - actual / anterior) * 100)
        if not MIN_DESCUENTO <= descuento <= MAX_DESCUENTO:
            return None
    else:
        # Sin precio anterior (muy común en estos grupos): la única regla es que el propio
        # mensaje declare un descuento de 50-99 %, en cualquier tienda.
        declarado = descuento_declarado(seg)
        if declarado is not None:
            if not MIN_DESCUENTO <= declarado <= MAX_DESCUENTO:
                return None
            descuento = declarado
    titulo = _titulo(seg, url)
    if len(titulo) < 5:
        return None
    low = seg.lower()
    marca, _ = detect_priority_brand({"titulo": titulo})
    return {
        "tienda": tienda,
        "titulo": titulo[:180],
        "marca": marca,
        "categoria": infer_category({"titulo": titulo, "marca": marca}),
        "precio_actual": actual,
        "precio_anterior": anterior,
        "descuento": descuento,
        "descuento_declarado": declarado,
        "url": url,
        "liquidacion": any(x in low for x in _LIQUIDACION),
        "outlet": any(x in low for x in ("outlet", "reacondicionado", "open box")),
        "condiciones": _condiciones(seg),
        "origen": origen,
        "origen_link": "Telegram",
        "origen_url": origen_url,
        "publicacion": html.unescape(seg[:4000]),
        "prioridad_fuente": prioridad,
        "bomba_candidata": BOMBA_MIN <= descuento <= BOMBA_MAX,
    }


def candidatos_de_mensaje(texto, enlaces, origen, *, prioridad=0, tachados=(), resolver=None, origen_url=None) -> list[dict]:
    texto = str(texto or "").replace("\xa0", " ")
    urls = urls_de_tiendas(texto, enlaces, resolver)
    if not urls:
        return []
    salida = []
    for (url, tienda, _), (antes, despues) in zip(urls, _segmentos(texto, urls)):
        if antes is None:
            continue
        for seg in (antes, despues):
            item = _armar(seg, url, tienda, origen, prioridad, tachados, origen_url) if seg else None
            if item:
                salida.append(item)
                break
    return salida


# ---------------------------------------------------------------------------
# Fuentes configuradas (fuentes_liquidaciones.json)
# ---------------------------------------------------------------------------
_RE_HANDLE = re.compile(r"t\.me/(?:s/)?(?!\+|joinchat)([A-Za-z0-9_]{4,32})", re.I)
_RE_INVITE = re.compile(r"(?:t\.me/(?:\+|joinchat/)|^\+)?([A-Za-z0-9_-]{12,})$")


def _hash_invitacion(valor) -> str | None:
    v = str(valor or "").strip().split("?")[0].rstrip("/")
    m = _RE_INVITE.search(v) if v else None
    return m.group(1) if m else None


def cargar_fuentes(ruta: str | None = None) -> list[dict]:
    try:
        with open(ruta or FUENTES_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return [dict(f) for f in FUENTES_POR_DEFECTO]
    fuentes = []
    for e in data.get("fuentes", []) if isinstance(data, dict) else []:
        if not isinstance(e, dict) or not str(e.get("tipo", "")).lower().startswith("telegram"):
            continue
        if e.get("autolectura") is not True or str(e.get("estado", "")).lower() not in ("activo", "activa"):
            continue
        try:
            prioridad = int(e.get("prioridad") or 0)
        except (TypeError, ValueError):
            prioridad = 0
        f = {"nombre": str(e.get("nombre") or "Telegram"), "prioridad": prioridad}
        if str(e.get("tipo", "")).lower() == "telegram_privado" or str(e.get("lectura", "")).lower() == "telethon":
            invite = e.get("invite") or os.environ.get(str(e.get("invite_env") or "")) or ""
            chat = (os.environ.get(str(e.get("chat_id_env") or "")) or "").strip()
            try:
                chat_id = int(chat) if chat else None
            except ValueError:
                chat_id = None
            f.update(modo="telethon", invite=_hash_invitacion(invite), chat_id=chat_id,
                     titulo_hint=str(e.get("titulo_hint") or "").lower())
        else:
            m = _RE_HANDLE.search(str(e.get("url") or e.get("handle") or ""))
            if not m:
                continue
            f.update(modo="web", handle=m.group(1))
        fuentes.append(f)
    return fuentes


# ---------------------------------------------------------------------------
# Lector público (vista web t.me/s/<canal>, sin credenciales)
# ---------------------------------------------------------------------------
def _sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": HONEST_USER_AGENT, "Accept-Language": "es-MX,es;q=0.9,en;q=0.7"})
    return s


def _fecha_post(post) -> datetime | None:
    nodo = post.select_one("time[datetime]")
    if not nodo:
        return None
    try:
        fecha = datetime.fromisoformat(nodo["datetime"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)


def parsear_vista_publica(html_texto: str, origen: str, prioridad: int = 0, *, resolver=None, ahora=None,
                          max_edad_h: float | None = None, limite: int | None = None) -> tuple[list[dict], int]:
    """(candidatos, publicaciones_visibles) a partir del HTML de ``t.me/s/<canal>``."""
    max_edad_h = MAX_EDAD_HORAS if max_edad_h is None else max_edad_h
    limite = MAX_POSTS_POR_CANAL if limite is None else limite
    ahora = ahora or datetime.now(timezone.utc)
    desde = ahora - timedelta(hours=max_edad_h)
    posts = BeautifulSoup(html_texto or "", "html.parser").select(".tgme_widget_message")
    items = []
    for post in posts[-limite:]:
        nodo = post.select_one(".tgme_widget_message_text")
        if not nodo:
            continue
        fecha = _fecha_post(post)
        if fecha and fecha < desde:
            continue
        enlaces = [a["href"] for a in nodo.select("a[href]") if a.get("href")]
        tachados = [s.get_text(" ", strip=True) for s in nodo.select("s, del, strike")]
        for br in nodo.select("br"):
            br.replace_with("\n")
        texto = re.sub(r"\n{3,}", "\n\n", nodo.get_text(""))
        permalink = post.select_one("a.tgme_widget_message_date")
        items.extend(candidatos_de_mensaje(texto, enlaces, origen, prioridad=prioridad, tachados=tachados,
                                           resolver=resolver, origen_url=permalink.get("href") if permalink else None))
    return items, len(posts)


def _leer_canal_publico(sesion, handle: str) -> tuple[str | None, int | None]:
    """(html, http). Backoff conservador; ante 401/403/404/451 no reintenta (SourceCircuit)."""
    circuito, espera, status = SourceCircuit(handle), 2.0, None
    for intento in range(3):
        try:
            r = sesion.get(f"https://t.me/s/{handle}", timeout=(5, 20))
        except requests.RequestException:
            status = None
        else:
            status = r.status_code
            if status < 400:
                return r.text, status
            circuito.record(status)
            if not circuito.can_continue() or intento == 2:
                return None, status
            circuito.backoff(status, intento, r.headers.get("Retry-After"))
            continue
        if intento < 2:
            time.sleep(espera)
            espera *= 2
    return None, status


def buscar_telegram_publico(sesion: requests.Session | None = None) -> list[dict]:
    global STATUS_PUBLICO
    fuentes = [f for f in cargar_fuentes() if f["modo"] == "web"]
    if not fuentes:
        STATUS_PUBLICO = {"state": "not_configured", "canales": []}
        return []
    sesion = sesion or _sesion()
    resolver = _crear_resolvedor(sesion)
    resultados, canales, codigos = [], [], []
    for f in sorted(fuentes, key=lambda x: -x["prioridad"]):
        info = {"canal": f["nombre"], "estado": "ok", "publicaciones": 0, "candidatos": 0}
        try:
            cuerpo, status = _leer_canal_publico(sesion, f["handle"])
            if cuerpo is None:
                info["estado"] = classify_status(status)
                codigos.append(status)
            else:
                items, visibles = parsear_vista_publica(cuerpo, f["nombre"], f["prioridad"], resolver=resolver)
                info.update(publicaciones=visibles, candidatos=len(items))
                if not visibles:
                    info["estado"] = "preview_unavailable"
                resultados.extend(items)
        except Exception as exc:  # un canal defectuoso no detiene a los demás
            info["estado"] = "error"
            logger.warning("Telegram/%s: error procesando (%s)", f["nombre"], type(exc).__name__)
        canales.append(info)
        logger.info("Telegram público/%s: %s, %d publicaciones, %d candidatos", f["nombre"], info["estado"],
                    info["publicaciones"], info["candidatos"])
        time.sleep(0.5)
    respondieron = [c for c in canales if c["estado"] in ("ok", "preview_unavailable")]
    if respondieron:
        STATUS_PUBLICO = {"state": "ok", "canales": canales}
    else:
        dominante = max((c["estado"] for c in canales), key=[c["estado"] for c in canales].count)
        STATUS_PUBLICO = {"state": dominante, "canales": canales, "http": [c for c in codigos if c]}
    return resultados


# ---------------------------------------------------------------------------
# Lector privado (Telethon, cuenta de usuario ya unida al grupo)
# ---------------------------------------------------------------------------
def _credenciales() -> tuple[int, str, str] | None:
    api_id = (os.environ.get("TELEGRAM_API_ID") or "").strip()
    api_hash = (os.environ.get("TELEGRAM_API_HASH") or "").strip()
    sesion = (os.environ.get("TELEGRAM_SESSION_STRING") or "").strip()
    if not (api_id and api_hash and sesion):
        return None
    try:
        return int(api_id), api_hash, sesion
    except ValueError:
        return None


def _mensaje_a_dict(m) -> dict:
    enlaces, tachados = [], []
    try:
        for ent, txt in m.get_entities_text() or []:
            nombre = type(ent).__name__
            if nombre == "MessageEntityTextUrl" and getattr(ent, "url", None):
                enlaces.append(ent.url)
            elif nombre == "MessageEntityUrl":
                enlaces.append(txt)
            elif nombre == "MessageEntityStrike":
                tachados.append(txt)
    except Exception:  # entidades ilegibles: se sigue con el texto plano
        pass
    try:
        for fila in m.buttons or []:
            enlaces.extend(b.url for b in fila if getattr(b, "url", None))
    except Exception:
        pass
    return {"id": m.id, "fecha": m.date, "texto": m.raw_text or m.message or "", "enlaces": enlaces, "tachados": tachados}


async def _resolver_entidad(client, canal: dict):
    """Chat por id, por invitación (solo comprueba; unirse requiere TELEGRAM_AUTO_JOIN) o por título."""
    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

    dialogos = await client.get_dialogs(limit=300)
    chat_id = canal.get("chat_id")
    if chat_id is not None:
        for d in dialogos:
            if d.id == chat_id or abs(d.id) in (abs(chat_id), abs(chat_id) - 1_000_000_000_000):
                return d.entity
    if canal.get("invite"):
        try:
            info = await client(CheckChatInviteRequest(canal["invite"]))
            if getattr(info, "chat", None) is not None:  # ChatInviteAlready: ya es miembro
                return info.chat
            if (os.environ.get("TELEGRAM_AUTO_JOIN") or "").lower() in ("1", "true", "yes"):
                actualizacion = await client(ImportChatInviteRequest(canal["invite"]))
                if getattr(actualizacion, "chats", None):
                    return actualizacion.chats[0]
        except Exception as exc:
            logger.warning("Telegram privado: invitación no utilizable (%s)", type(exc).__name__)
    hint = canal.get("titulo_hint")
    if hint:
        for d in dialogos:
            if hint in (d.name or "").lower():
                return d.entity
    return None


async def _mensajes_privados(canal: dict, api_id: int, api_hash: str, sesion: str,
                             max_edad_h: float, limite: int) -> tuple[list[dict], str]:
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession

    def _tipos(*nombres):
        return tuple(getattr(errors, n) for n in nombres if hasattr(errors, n))

    sesion_invalida = _tipos("AuthKeyDuplicatedError", "AuthKeyUnregisteredError", "UserDeactivatedError",
                             "UserDeactivatedBanError", "SessionRevokedError", "SessionExpiredError")
    sin_acceso = _tipos("ChannelPrivateError", "ChatForbiddenError", "ChatAdminRequiredError")
    flood = _tipos("FloodWaitError")

    mensajes: list[dict] = []
    estado = "ok"
    client = TelegramClient(StringSession(sesion), api_id, api_hash, connection_retries=2, retry_delay=2,
                            request_retries=2, timeout=15, flood_sleep_threshold=20, receive_updates=False)
    try:
        await client.connect()
        if not await client.is_user_authorized():  # nunca se pide login interactivo en CI
            return [], "session_invalid"
        entidad = await _resolver_entidad(client, canal)
        if entidad is None:
            return [], "chat_not_found"
        desde = datetime.now(timezone.utc) - timedelta(hours=max_edad_h)
        async for m in client.iter_messages(entidad, limit=limite):
            if m.date and m.date < desde:
                break
            if m.raw_text or m.message:
                mensajes.append(_mensaje_a_dict(m))
    except sesion_invalida:
        estado = "session_invalid"
    except sin_acceso:
        estado = "forbidden"
    except flood:
        estado = "flood_wait"  # se conserva lo ya leído
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return mensajes, estado


def buscar_telegram_privado() -> list[dict]:
    global STATUS_PRIVADO
    canales = [f for f in cargar_fuentes() if f["modo"] == "telethon"]
    if not canales:
        STATUS_PRIVADO = {"state": "not_configured", "canales": []}
        return []
    creds = _credenciales()
    if creds is None:
        STATUS_PRIVADO = {"state": "not_configured", "canales": [],
                          "motivo": "faltan TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION_STRING"}
        logger.warning("Telegram privado omitido: credenciales de sesión no configuradas.")
        return []
    resolver = _crear_resolvedor(_sesion())
    resultados, info_canales = [], []
    for canal in sorted(canales, key=lambda x: -x["prioridad"]):
        info = {"canal": canal["nombre"], "estado": "error", "mensajes": 0, "candidatos": 0}
        try:
            mensajes, estado = asyncio.run(asyncio.wait_for(
                _mensajes_privados(canal, *creds, MAX_EDAD_HORAS, MAX_MENSAJES_PRIVADO), PRIVADO_TIMEOUT))
            info.update(estado=estado, mensajes=len(mensajes))
            for m in mensajes:
                resultados_msg = candidatos_de_mensaje(m["texto"], m["enlaces"], canal["nombre"],
                                                       prioridad=canal["prioridad"], tachados=m["tachados"],
                                                       resolver=resolver)
                info["candidatos"] += len(resultados_msg)
                resultados.extend(resultados_msg)
        except asyncio.TimeoutError:
            info["estado"] = "timeout"
        except Exception as exc:  # la sesión de Telegram jamás tumba el ciclo
            info["estado"] = "error"
            logger.warning("Telegram privado/%s: %s", canal["nombre"], type(exc).__name__)
        info_canales.append(info)
        logger.info("Telegram privado/%s: %s, %d mensajes, %d candidatos", canal["nombre"], info["estado"],
                    info["mensajes"], info["candidatos"])
    peor = [c["estado"] for c in info_canales if c["estado"] != "ok"]
    STATUS_PRIVADO = {"state": peor[0] if peor else "ok", "canales": info_canales}
    return resultados
