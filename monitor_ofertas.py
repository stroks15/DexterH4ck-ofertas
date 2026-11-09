# monitor_ofertas.py
import html
import importlib
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from core.liquidation_engine import evaluate_product, calculate_discount
from core.extreme_liquidation import analizar_precio_extremo
from core import supabase_history
from core.price_sanitizer import coincide_con_medida, sanear_par_precios, texto_del_item, sanitize_history
from core.offer_identity import canonical_store, deduplicate_candidates, identity_keys, history_key
from core.product_identifiers import canonical_product_identifier
from core.source_resilience import HONEST_USER_AGENT, classify_status

# Integración de las capas de servicios: API-first + legacy en paralelo
from scrapers.api_stores import ApiStoresScraper

try:
    from scrapers.vtex_stores import VtexStoresScraper
except ImportError:
    # La capa VTEX es opcional: no debe impedir cargar el monitor ni sus regresiones.
    VtexStoresScraper = None

# Configuración del motor de registro de eventos
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DexterH4ck.Orchestrator")

MIN_DESCUENTO = 50
MAX_DESCUENTO = 99
HISTORIAL_FILE = "historial_ofertas.json"
MAX_HISTORIAL = 10000
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

_ultimo_envio_telegram = 0.0

TIENDAS_OBJETIVO = (
    "Walmart MX",
    "Bodega Aurrera",
    "Chedraui",
    "Soriana",
    "Liverpool",
    "Amazon MX",
    "Mercado Libre MX",
    "Coppel",
    "Suburbia",
    "Oferstock",
)


def cargar_historial():
    if not os.path.exists(HISTORIAL_FILE):
        return {}
    try:
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as archivo:
            data = json.load(archivo)
            return _sanear_historial(data) if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _sanear_historial(historial: dict) -> dict:
    """Repara precios históricos contaminados por gramajes antes de usarlos."""
    historial, reparadas = sanitize_history(historial)
    if reparadas:
        logger.warning("Historial: %d campos contaminados fueron reparados.", reparadas)
    return historial


_HISTORIAL_LOCK = threading.RLock()


def guardar_historial(historial):
    """Escritura atómica del historial local, sincronizada entre hilos del proceso."""
    with _HISTORIAL_LOCK:
        datos = historial
        if len(datos) > MAX_HISTORIAL:
            datos = dict(
                sorted(
                    datos.items(),
                    key=lambda par: (
                        par[1].get("ultima_actualizacion", "")
                        if isinstance(par[1], dict)
                        else ""
                    ),
                    reverse=True,
                )[:MAX_HISTORIAL]
            )
        temporal = f"{HISTORIAL_FILE}.tmp"
        try:
            with open(temporal, "w", encoding="utf-8") as archivo:
                json.dump(datos, archivo, ensure_ascii=False, indent=2)
                archivo.flush()
                os.fsync(archivo.fileno())
            os.replace(temporal, HISTORIAL_FILE)
        except OSError:
            try:
                if os.path.exists(temporal):
                    os.remove(temporal)
            except OSError:
                pass
            raise


def limpiar_titulo_producto(titulo, url=""):
    texto = html.unescape(str(titulo or ""))
    texto = re.sub(r"\s+", " ", texto).strip(" \t\r\n-–—|·")
    texto = re.split(
        r"\b(?:precio\s+(?:actual|final|de\s+oferta)|antes|ahorra|hasta\s+\d+\s+mensualidades?|mensualidades?\s+fijas?|precio\s+anterior|precio\s+regular)\b",
        texto,
        maxsplit=1,
        flags=re.I,
    )
    if isinstance(texto, list) and len(texto) > 0:
        texto = texto[0]
    texto = re.sub(r"(?:^|[|·–—-])\s*\$\s*[0-9][0-9,]*(?:\s+[0-9]{2})?(?:\.[0-9]{1,2})?", " ", str(texto))
    texto = re.sub(r"\$\s*[0-9][0-9,]*(?:\s+[0-9]{2})?(?:\.[0-9]{1,2})?", " ", texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip(" \t\r\n-–—|·,;:")
    letras = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü]{2,}", texto)
    if len("".join(letras)) < 5 and url:
        try:
            from urllib.parse import unquote

            path = unquote(urlparse(url).path).rstrip("/")
            slug = path.rsplit("/", 1)[-1]
            slug = re.sub(r"(?:-)?(?:mlm[-_]?)?\d{5,}$", "", slug, flags=re.I)
            slug = re.sub(r"[-_]+", " ", slug)
            slug = re.sub(r"\b(?:ip|pdp|producto|product|item)\b", " ", slug, flags=re.I)
            slug = re.sub(r"\s{2,}", " ", slug).strip(" -_/")
            if len(slug) >= 5:
                texto = slug
        except Exception:
            pass
    return texto[:180]


def formato_alerta_tipo(tipo):
    return "🟢" if tipo == "VERDE" else "🔴"


def es_enlace_producto_directo(url):
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not host or host in ("t.me", "telegram.me", "google.com", "www.google.com"):
        return False
    if any(
        x in path for x in ("/search", "/buscar", "/ofertas", "/oferta", "/catalogo", "/marcas", "/home", "/social/")
    ):
        return False
    patrones_por_tienda = {
        "walmart.com.mx": ("/ip/",),
        "bodegaaurrera.com.mx": ("/ip/",),
        "chedraui.com.mx": ("/p/",),
        "coppel.com": ("/pdp/",),
        "amazon.com.mx": ("/dp/", "/gp/product/"),
        "mercadolibre.com.mx": ("/mlm-", "/p/"),
        "liverpool.com.mx": ("/tienda/pdp/", "/pdp/"),
        "soriana.com": ("/producto/", "/p/"),
        "suburbia.com.mx": ("/p/", "/producto/"),
    }
    for dominio, patrones in patrones_por_tienda.items():
        if host == dominio or host.endswith("." + dominio):
            if dominio == "chedraui.com.mx":
                return "/p/" in path or path.endswith("/p")
            if dominio == "soriana.com" and re.search(r"/\d{5,}\.html$", path):
                return True
            return any(p in path for p in patrones)
    return len(path.strip("/")) > 12


def es_enlace_valido_sin_error(url: str) -> bool:
    """Rechaza URLs sin esquema/host y marcadores sin resolver en ruta o query."""
    from tools.telegram_bot import clean_product_url
    return clean_product_url(url) is not None


def validar_y_filtrar_bomba(item: dict, historial_local: dict) -> bool:
    """Valida una alerta del 95–99 % usando exclusivamente history_key(item)."""
    try:
        precio_actual = _num(item.get("precio_actual") or item.get("price"))
        precio_anterior = _num(item.get("precio_anterior"))
        descuento = _num(item.get("descuento"))

        if not descuento and precio_actual > 0 and precio_anterior > precio_actual:
            descuento = round((1 - precio_actual / precio_anterior) * 100)

        # Esta función se usa como filtro específico para bombas. No debe bloquear
        # candidatos que no pertenezcan al rango crítico.
        if not 95 <= descuento <= 99:
            return True

        clave = history_key(item)
        if not isinstance(clave, str) or not clave.strip():
            logger.warning("[BOMBA RECHAZADA] history_key(item) vacío.")
            return False

        entrada = historial_local.get(clave)
        if isinstance(entrada, dict) and entrada.get("precio_alertado") is not None:
            logger.info("[BOMBA OMITIDA] Ya alertada localmente: %s", clave)
            return False

        if not es_enlace_valido_sin_error(item.get("url", "")):
            logger.warning("[BOMBA RECHAZADA] Enlace inválido para %s.", clave)
            return False

        central = supabase_history.ya_enviada(clave)
        if central is True:
            logger.info("[BOMBA OMITIDA] Ya registrada en Supabase: %s", clave)
            return False
        if central is None and supabase_history.habilitado():
            logger.warning(
                "[BOMBA] No se pudo verificar Supabase; se usará el historial local. llave=%s",
                clave,
            )

        logger.info("[BOMBA VALIDADA] Descuento=%s%% llave=%s", descuento, clave)
        return True
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        logger.warning("[BOMBA RECHAZADA] Datos inválidos (%s).", type(exc).__name__)
        return False
    except Exception as exc:
        logger.warning("[BOMBA RECHAZADA] Error inesperado (%s).", type(exc).__name__)
        return False


def _candidato_de_tienda_objetivo(item):
    if str(item.get("tipo_fuente") or "").upper() == "FISICA":
        return True
    tienda = str(item.get("tienda") or item.get("store") or "").strip().lower()
    aliases = (
        ("walmart", "Walmart MX"),
        ("bodega aurrera", "Bodega Aurrera"),
        ("chedraui", "Chedraui"),
        ("soriana", "Soriana"),
        ("liverpool", "Liverpool"),
        ("amazon", "Amazon MX"),
        ("mercado libre", "Mercado Libre MX"),
        ("mercadolibre", "Mercado Libre MX"),
        ("coppel", "Coppel"),
        ("suburbia", "Suburbia"),
        ("oferstock", "Oferstock"),
    )
    if any(alias in tienda for alias, _ in aliases):
        return True
    origen = str(item.get("origen_link") or item.get("origen") or "").lower()
    return origen in ("telegram", "liquidazona", "whatsapp") and bool(item.get("url"))


DESCUENTO_BOMBA = 90  # Campo de prioridad; el formato distingue ⚡ (90–94) de 💣 (95–99).
MAX_ALERTAS_POR_CICLO = int(os.environ.get("MAX_ALERTAS_POR_CICLO", "25") or 25)
FUENTES_TIMEOUT_TOTAL = float(os.environ.get("FUENTES_TIMEOUT_TOTAL", "540") or 540)
FUENTES_CONCURRENCIA = 6
MONITOR_HEALTH_FILE = "monitor_health.json"

# Estados de fuente que NO son un fallo (no hubo error real de acceso).
ESTADOS_SIN_FALLO = {"ok", "ok_via_proxy", "empty", "not_configured", "skipped_preflight"}
# Estados que indican que la fuente sí respondió con datos o vacía (para detectar caída total).
ESTADOS_RESPONDIO = {"ok", "empty"}

VARIABLES_OPCIONALES = (
    "WALMART_GRAPHQL_URL",
    "WALMART_STORE_ID",
    "WALMART_GRAPHQL_QUERY",
    "BODEGA_GRAPHQL_URL",
    "BODEGA_STORE_ID",
    "BODEGA_GRAPHQL_QUERY",
    "CHEDRAUI_VTEX_ENDPOINT",
    "SORIANA_API_ENDPOINT",
    "COPPEL_API_ENDPOINT",
    "COPPEL_VTEX_ENDPOINT",
    "LIVERPOOL_VTEX_ENDPOINT",
    "SCRAPER_API_KEY",
    "SCRAPER_API_ENDPOINT",
    "MAX_PROXY_CREDITS_PER_CYCLE",
    "SUBURBIA_API_ENDPOINT",
    "AMAZON_API_ENDPOINT",
    "ML_QUERIES",
    "MERCADOLIBRE_ACCESS_TOKEN",
    "APPS_SCRIPT_PROXY_URL",
    "LEGACY_SEARCH_URLS_JSON",
)


# ---------------------------------------------------------------------------
# Precios y referencia
# ---------------------------------------------------------------------------
def _num(valor) -> float:
    try:
        numero = float(valor)
        return numero if numero > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def es_origen_telegram(item: dict) -> bool:
    """Reconoce mensajes procedentes de Telegram aunque el canal tenga otro nombre."""
    campos = ("origen_link", "origen", "fuente", "source", "canal", "channel", "nombre_fuente")
    valores = " ".join(str(item.get(c) or "") for c in campos).casefold()
    normalizado = re.sub(r"[^a-z0-9]+", " ", valores).strip()
    return any(token in normalizado for token in ("telegram", "grupo walmart channel", "walmart channel"))


def descuento_declarado_telegram(item: dict) -> int:
    """Descuento declarado válido para mensajes de Telegram (50–99%)."""
    if not es_origen_telegram(item):
        return 0
    valor = _num(item.get("descuento_declarado", item.get("descuento", 0)))
    return int(round(valor)) if MIN_DESCUENTO <= valor <= MAX_DESCUENTO else 0

def calcular_datos(item: dict, entrada_historial: dict | None = None):
    """Devuelve (precio_actual, precio_referencia, descuento).

    La referencia es conservadora: se usa el precio anterior explícito de la oferta
    si es válido; solo si no existe se recurre al precio máximo del historial.
    Ambos pasan por la sanitización estricta: un gramaje/tamaño ("875g", "1kg", "400ml")
    jamás puede actuar como precio de referencia.
    """
    texto = texto_del_item(item)
    actual, anterior, motivo = sanear_par_precios(
        item.get("precio_actual", item.get("price")),
        item.get("precio_anterior", item.get("previous_price")),
        texto,
    )
    if motivo and motivo not in ("anterior_no_mayor",):
        logger.warning(
            "[PRECIO SANEADO] %s | %s | actual=%r anterior=%r -> %s",
            item.get("tienda"),
            str(item.get("titulo") or "")[:60],
            item.get("precio_actual"),
            item.get("precio_anterior"),
            motivo,
        )
    actual = actual or 0.0
    maximo = _num((entrada_historial or {}).get("precio_maximo"))
    if maximo and coincide_con_medida(maximo, texto):
        maximo = 0.0  # historial contaminado por un gramaje
    if actual > 0 and anterior and anterior > actual:
        referencia = anterior
    elif actual > 0 and maximo > actual:
        referencia = maximo
    else:
        referencia = 0.0
    descuento = calculate_discount(referencia, actual) if referencia else 0
    return actual, referencia, descuento


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def _telegram_api(metodo: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{metodo}"


def enviar_telegram(texto, imagen=None, sticker_id=None) -> bool:
    """Envía un mensaje. Devuelve True solo si Telegram confirmó la entrega."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
    global _ultimo_envio_telegram

    session = requests.Session()
    espera = 3.2 - (time.monotonic() - _ultimo_envio_telegram)
    if espera > 0:
        time.sleep(espera)

    if sticker_id:
        try:
            session.post(
                _telegram_api("sendSticker"), data={"chat_id": TELEGRAM_CHAT_ID, "sticker": sticker_id}, timeout=15
            )
        except requests.RequestException as exc:
            logger.warning("Telegram sticker error: %s", type(exc).__name__)

    metodo = "sendPhoto" if imagen else "sendMessage"
    for intento in range(3):
        try:
            if metodo == "sendPhoto":
                response = session.post(
                    _telegram_api(metodo),
                    data={"chat_id": TELEGRAM_CHAT_ID, "photo": imagen, "caption": texto[:1000], "parse_mode": "MarkdownV2"},
                    timeout=25,
                )
            else:
                response = session.post(
                    _telegram_api(metodo),
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": texto,
                        "parse_mode": "MarkdownV2",
                        "disable_web_page_preview": False,
                    },
                    timeout=20,
                )
            _ultimo_envio_telegram = time.monotonic()
            if response.status_code == 200:
                return True
            # Un error de formato MarkdownV2 no debe bloquear la alerta:
            # reintentar como texto plano si Telegram no puede parsear entidades.
            if response.status_code == 400:
                try:
                    error_payload = response.json()
                    description = str(error_payload.get('description', ''))
                except (ValueError, AttributeError):
                    description = ''
                logger.error('Telegram HTTP 400 en %s: %s', metodo, description[:300] or 'sin descripción')
                if "can't parse entities" in description.casefold() or 'parse entities' in description.casefold():
                    try:
                        plain_data = (
                            {'chat_id': TELEGRAM_CHAT_ID, 'photo': imagen, 'caption': texto[:1000]}
                            if metodo == 'sendPhoto'
                            else {'chat_id': TELEGRAM_CHAT_ID, 'text': texto, 'disable_web_page_preview': False}
                        )
                        plain_response = session.post(
                            _telegram_api(metodo), data=plain_data,
                            timeout=25 if metodo == 'sendPhoto' else 20,
                        )
                        _ultimo_envio_telegram = time.monotonic()
                        if plain_response.status_code == 200:
                            logger.warning('Telegram: alerta enviada como texto plano tras error de MarkdownV2.')
                            return True
                        try:
                            plain_detail = plain_response.json().get('description', '')
                        except (ValueError, AttributeError):
                            plain_detail = ''
                        logger.error('Telegram reintento sin Markdown HTTP %s: %s', plain_response.status_code, str(plain_detail)[:300])
                    except requests.RequestException as exc:
                        logger.warning('Telegram reintento sin Markdown falló (%s).', type(exc).__name__)
                if metodo == 'sendPhoto':
                    metodo, imagen = 'sendMessage', None
                    continue
                return False
            if response.status_code == 429:
                try:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 5))
                except (ValueError, TypeError, AttributeError):
                    retry_after = 5
                time.sleep(min(max(retry_after, 1), 60) + 0.5)
                continue
            logger.error("Telegram HTTP %s en %s", response.status_code, metodo)
            if metodo == "sendPhoto":  # la foto falló: se reintenta como texto
                metodo, imagen = "sendMessage", None
                continue
            if response.status_code in (400, 401, 403, 404):
                return False  # credencial/chat inválidos: reintentar no ayuda
        except requests.RequestException as exc:
            logger.warning("Telegram error de red (%s) en intento %s", type(exc).__name__, intento + 1)
            if metodo == "sendPhoto":
                metodo, imagen = "sendMessage", None
            time.sleep(1.5)
    return False


def formatear_alerta(alerta: dict) -> str:
    """Format a tiered alert using Telegram MarkdownV2 (single source of truth)."""
    from tools.telegram_bot import format_alert
    evaluacion = alerta.get("evaluacion") or {}
    item = alerta.get("item") or {}
    item.setdefault("marca", evaluacion.get("marca", ""))
    item.setdefault("categoria", evaluacion.get("categoria", ""))
    alerta["item"] = item
    return format_alert(alerta)


# ---------------------------------------------------------------------------
# Orquestación: cada fuente/tienda está aislada
# ---------------------------------------------------------------------------
@dataclass
class Fuente:
    nombre: str
    tiendas: set
    funcion: Callable[[], list]
    estado_fn: Callable[[], Any] = None
    resultado: list = field(default_factory=list)
    abandonada: threading.Event = field(default_factory=threading.Event)


ULTIMO_REPORTE_FUENTES: dict = {}

# ---------------------------------------------------------------------------
# Registro de respuestas HTTP por fuente.
# Varios scrapers legacy imprimen "HTTP 403" y devuelven [] sin exponer el estado.
# Este registro (por hilo, solo observa) evita reportar como "empty"/"ok" una fuente
# cuyas peticiones fueron todas rechazadas.
# ---------------------------------------------------------------------------
_TLS = threading.local()
_SEND_INSTALADO = False


def _instalar_registro_http() -> None:
    global _SEND_INSTALADO
    if _SEND_INSTALADO:
        return
    original = requests.sessions.Session.send

    def _send_registrado(self, request, **kwargs):
        registro = getattr(_TLS, "http", None)
        try:
            respuesta = original(self, request, **kwargs)
        except Exception as exc:
            if registro is not None:
                registro.append(exc)
            raise
        if registro is not None:
            registro.append(respuesta.status_code)
        return respuesta

    requests.sessions.Session.send = _send_registrado
    _SEND_INSTALADO = True


def estado_desde_http(registro: list) -> str | None:
    """None si no hay información o si alguna petición fue exitosa; si todas fallaron, el estado dominante."""
    if not registro:
        return None
    codigos = [r for r in registro if isinstance(r, int)]
    if any(200 <= c < 400 for c in codigos):
        return None
    if codigos:
        estados = [classify_status(c) for c in codigos]
        return max(set(estados), key=estados.count)
    return "timeout" if any(isinstance(r, requests.Timeout) for r in registro) else "network_error"


def _lazy(modulo: str, nombre: str, *args, **kwargs) -> Callable[[], list]:
    """Importa el scraper al ejecutarse: un ImportError afecta solo a ESA fuente."""

    def _llamar():
        return getattr(importlib.import_module(modulo), nombre)(*args, **kwargs)

    return _llamar


def _lazy_attr(modulo: str, atributo: str) -> Callable[[], Any]:
    def _leer():
        return getattr(importlib.import_module(modulo), atributo, None)

    return _leer


def _crear_sesion() -> requests.Session:
    sesion = requests.Session()
    sesion.headers.update(
        {
            "User-Agent": HONEST_USER_AGENT,
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
        }
    )
    return sesion


def _tiendas_bloqueadas() -> set:
    """Tiendas que no pasaron el preflight (PREFLIGHT_BLOCKED_STORES), en forma canónica."""
    bruto = os.environ.get("PREFLIGHT_BLOCKED_STORES", "")
    return {canonical_store(t) for t in bruto.split(",") if t.strip()}


def _estado_tiendas_mexico():
    from scrapers import tiendas_mexico

    estados = [
        value if isinstance(value, dict) else {"state": str(value)}
        for value in tiendas_mexico.LAST_STATUS.values()
    ]
    if not estados:
        return {"state": "not_run"}
    if any(value.get("state") == "ok" for value in estados):
        return {"state": "ok", "stores": estados}
    if any(value.get("state") == "empty" for value in estados):
        return {"state": "empty", "stores": estados}
    first = estados[0]
    return {"state": first.get("state", "unknown"), "http": first.get("http_status"), "reason": first.get("reason")}


def _estado_modulo(modulo: str):
    return lambda: dict(getattr(importlib.import_module(modulo), "LAST_STATUS", {}) or {})


def definir_fuentes(sesion: requests.Session | None = None) -> list:
    sesion = sesion or _crear_sesion()
    fuentes: list[Fuente] = []

    # API-first: un adaptador aislado por tienda
    for scraper in ApiStoresScraper().scrapers:
        fuentes.append(
            Fuente(
                f"api:{scraper.store}",
                {canonical_store(scraper.store)},
                scraper.discover,
                lambda s=scraper: s.last_status,
            )
        )

    # Proxy unificado en Google Apps Script (Walmart, Bodega, Mercado Libre, Chedraui, Soriana).
    # Sin tiendas asociadas: un preflight bloqueado de una tienda no debe apagar el proxy completo.
    from scrapers.google_proxy_scraper import GoogleProxyScraper

    proxy = GoogleProxyScraper()
    fuentes.append(Fuente("proxy:google_apps_script", set(), proxy.discover, lambda: proxy.last_status))

    # Legacy/browser en paralelo
    fuentes.append(
        Fuente(
            "legacy:tiendas_mexico",
            {"amazon", "soriana", "liverpool"},
            _lazy("scrapers.tiendas_mexico", "buscar_todas"),
            _estado_tiendas_mexico,
        )
    )
    fuentes.append(
        Fuente(
            "legacy:liquidaciones_oficiales",
            set(),
            _lazy("scrapers.liquidaciones_oficiales", "buscar_liquidaciones_oficiales", sesion),
        )
    )
    fuentes.append(
        Fuente("legacy:tiendas_fisicas", set(), _lazy("scrapers.tiendas_fisicas", "buscar_tiendas_fisicas", sesion))
    )
    fuentes.append(Fuente("comunidad:telegram", set(), _lazy("scrapers.telegram_ofertas", "buscar_telegram", sesion)))
    # Telegram (adaptador unificado): dos workers aislados. Si la sesión de Telethon se retrasa o cae,
    # ni los canales públicos ni los scrapers de tiendas esperan por ella (cada fuente corre en su hilo).
    fuentes.append(
        Fuente(
            "comunidad:telegram_publico",
            set(),
            _lazy("scrapers.telegram_monitor_scraper", "buscar_telegram_publico", sesion),
            _lazy_attr("scrapers.telegram_monitor_scraper", "STATUS_PUBLICO"),
        )
    )
    fuentes.append(
        Fuente(
            "comunidad:telegram_privado",
            set(),
            _lazy("scrapers.telegram_monitor_scraper", "buscar_telegram_privado"),
            _lazy_attr("scrapers.telegram_monitor_scraper", "STATUS_PRIVADO"),
        )
    )
    fuentes.append(
        Fuente(
            "comunidad:whatsapp",
            set(),
            _lazy("scrapers.whatsapp_channels", "buscar_whatsapp"),
            _estado_modulo("scrapers.whatsapp_channels"),
        )
    )
    fuentes.append(
        Fuente("comunidad:feed", set(), _lazy("scrapers.feeds_comunidad_api", "parsear_feed_comunidad_espejo"))
    )
    fuentes.append(Fuente("comunidad:web", set(), _lazy("scrapers.comunidades_web", "buscar_comunidades_web")))
    fuentes.append(
        Fuente(
            "liquidazona:walmart",
            {canonical_store("Walmart MX")},
            _lazy("scrapers.liquidazona", "buscar_liquidazona_walmart", sesion),
        )
    )
    if os.environ.get("NETWORK_BROWSER_ENABLED", "false").strip().lower() in ("1", "true", "yes", "si"):
        fuentes.append(
            Fuente("browser:network_json", set(), _lazy("scrapers.browser_network", "buscar_network_browser"))
        )
    return fuentes


def _ejecutar_fuente(
    fuente: Fuente,
    salida: dict,
    semaforo: threading.Semaphore,
    salida_lock: threading.Lock,
) -> None:
    inicio = time.monotonic()
    registro = {"fuente": fuente.nombre, "estado": "error", "candidatos": 0, "segundos": 0.0}
    try:
        with semaforo:
            _TLS.http = []
            try:
                items = fuente.funcion() or []
                if not isinstance(items, list):
                    registro.update(estado="invalid_response", error=f"tipo inesperado: {type(items).__name__}")
                else:
                    resultados = [i for i in items if isinstance(i, dict)]
                    registro["candidatos"] = len(resultados)
                    estado = {}
                    if fuente.estado_fn:
                        try:
                            estado = fuente.estado_fn() or {}
                        except Exception as exc:
                            logger.debug("estado_fn de %s falló: %s", fuente.nombre, type(exc).__name__)
                    declarado = str(estado.get("state") or "")
                    if declarado and declarado not in ("not_run", "ok"):
                        registro["estado"] = declarado
                        if estado.get("http") is not None:
                            registro["http"] = estado["http"]
                    elif resultados:
                        registro["estado"] = "ok"
                    else:
                        registro["estado"] = estado_desde_http(getattr(_TLS, "http", [])) or "empty"
                    fuente.resultado = resultados
            except Exception as exc:
                registro.update(estado="error", error=f"{type(exc).__name__}: {exc}"[:300])
                logger.error("[%s] fallo aislado: %s", fuente.nombre, registro["error"])
    except Exception as exc:
        registro.update(estado="error", error=f"{type(exc).__name__}: {exc}"[:300])

    registro["segundos"] = round(time.monotonic() - inicio, 1)
    with salida_lock:

        # Un hilo ya expirado o en estado terminal de timeout no debe sobrescribir el reporte.

        if fuente.abandonada.is_set() or salida.get(fuente.nombre, {}).get("estado") == "timeout":

            logger.debug("[%s] terminó tarde; se conserva timeout.", fuente.nombre)

            return
        salida[fuente.nombre] = registro


def ejecutar_orquestacion_paralela(contexto_ficticio=None) -> list:
    """Ejecuta API-first primero y solo después recurre al Spider HTML legacy."""
    global ULTIMO_REPORTE_FUENTES
    logger.info("Iniciando despacho API-first de scrapers de liquidación...")
    _instalar_registro_http()
    fuentes = definir_fuentes()
    bloqueadas = _tiendas_bloqueadas()
    salida: dict = {}
    salida_lock = threading.Lock()
    semaforo = threading.Semaphore(FUENTES_CONCURRENCIA)
    limite = time.monotonic() + FUENTES_TIMEOUT_TOTAL

    legacy = next((f for f in fuentes if f.nombre == "legacy:tiendas_mexico"), None)
    primarias = [f for f in fuentes if f is not legacy]

    def ejecutar_grupo(grupo: list[Fuente]) -> None:
        hilos: list[tuple[Fuente, threading.Thread]] = []
        for fuente in grupo:
            if fuente.tiendas and fuente.tiendas <= bloqueadas:
                salida[fuente.nombre] = {
                    "fuente": fuente.nombre, "estado": "skipped_preflight",
                    "candidatos": 0, "segundos": 0.0,
                }
                logger.warning("[%s] omitida: la tienda no pasó el preflight (las demás continúan).", fuente.nombre)
                continue
            hilo = threading.Thread(
                target=_ejecutar_fuente,
                args=(fuente, salida, semaforo, salida_lock),
                name=f"src-{fuente.nombre}", daemon=True,
            )
            hilo.start()
            hilos.append((fuente, hilo))

        for fuente, hilo in hilos:
            hilo.join(max(0.0, limite - time.monotonic()))
            with salida_lock:
                if hilo.is_alive() and fuente.nombre not in salida:
                    fuente.abandonada.set()
                    salida[fuente.nombre] = {
                        "fuente": fuente.nombre, "estado": "timeout", "candidatos": 0,
                        "segundos": round(FUENTES_TIMEOUT_TOTAL, 1),
                    }
                    logger.error("[%s] timeout; se abandona esta fuente del ciclo y el monitor continúa.", fuente.nombre)

    # Fase 1: APIs oficiales/JSON, comunidad y fuentes no-legacy.
    ejecutar_grupo(primarias)

    candidatos: list = []
    tiendas_resueltas_por_api: set[str] = set()
    for fuente in primarias:
        if salida.get(fuente.nombre, {}).get("estado") == "timeout":
            continue
        candidatos.extend(fuente.resultado)
        if fuente.nombre.startswith(("api:", "proxy:")):
            for item in fuente.resultado:
                if isinstance(item, dict):
                    tiendas_resueltas_por_api.add(canonical_store(item.get("tienda") or item.get("store")))

    # Fase 2: el Spider HTML se ejecuta solo para tiendas que no devolvieron
    # productos desde la capa API-first. Una API vacía/no configurada permite fallback.
    if legacy is not None:
        from scrapers import tiendas_mexico
        tiendas_legacy = set(tiendas_mexico._configured_urls())
        pendientes = [
            name for name in tiendas_legacy
            if canonical_store(name) not in tiendas_resueltas_por_api
            and canonical_store(name) not in bloqueadas
        ]
        if pendientes and time.monotonic() < limite:
            legacy.funcion = lambda pending=tuple(sorted(pendientes)): tiendas_mexico.buscar_todas(filtro_tienda=pending)
            legacy.tiendas = {canonical_store(name) for name in pendientes}
            logger.info("Fallback Scrapling para tiendas sin resultados API: %s", ", ".join(sorted(pendientes)))
            ejecutar_grupo([legacy])
            if salida.get(legacy.nombre, {}).get("estado") != "timeout":
                candidatos.extend(legacy.resultado)
        else:
            tiendas_mexico.LAST_STATUS.clear()
            if not pendientes:
                estado = "skipped_api_priority"
                motivo = "Todas las tiendas legacy ya tuvieron resultados API-first."
            else:
                estado = "timeout"
                motivo = "Se agotó el presupuesto de tiempo antes del fallback Scrapling."
            tiendas_mexico.LAST_STATUS["legacy"] = {"state": estado, "reason": motivo}
            salida[legacy.nombre] = {
                "fuente": legacy.nombre, "estado": estado, "candidatos": 0, "segundos": 0.0,
            }

    ULTIMO_REPORTE_FUENTES = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preflight_bloqueadas": sorted(bloqueadas),
        "fuentes": [salida[f.nombre] for f in fuentes if f.nombre in salida],
    }
    for fila in ULTIMO_REPORTE_FUENTES["fuentes"]:
        nivel = logging.INFO if fila["estado"] in ESTADOS_SIN_FALLO or fila["estado"] == "skipped_api_priority" else logging.WARNING
        logger.log(nivel, "[FUENTE] %-34s estado=%-18s candidatos=%d", fila["fuente"], fila["estado"], fila["candidatos"])
    return candidatos


# ---------------------------------------------------------------------------
# Evaluación de candidatos
# ---------------------------------------------------------------------------
def _actualizar_historial(historial: dict, clave: str, item: dict, actual: float, referencia: float, ahora: str):
    entrada = historial.get(clave) if isinstance(historial.get(clave), dict) else None
    maximo_previo = _num((entrada or {}).get("precio_maximo"))
    maximo = max(maximo_previo, actual, referencia)
    if entrada is not None and entrada.get("precio_actual") == actual and maximo == maximo_previo:
        return entrada
    nueva = dict(entrada or {})
    nueva.update(
        {
            "tienda": item.get("tienda"),
            "titulo": item.get("titulo"),
            "url": item.get("url"),
            "precio_actual": actual,
            "precio_maximo": maximo,
            "ultima_actualizacion": ahora,
        }
    )
    nueva.setdefault("precio_alertado", None)
    historial[clave] = nueva
    return nueva


def procesar_candidatos(candidatos: list, historial: dict) -> list:
    """Telegram: la elegibilidad depende únicamente de descuento 50–99%; otras fuentes conservan filtros."""
    ahora = datetime.now(timezone.utc).isoformat()
    unicos, duplicados = deduplicate_candidates([c for c in candidatos if isinstance(c, dict)])
    logger.info("Candidatos: %d únicos (%d duplicados descartados).", len(unicos), duplicados)
    alertas = []

    for original in unicos:
        try:
            es_telegram = es_origen_telegram(original)
            if not es_telegram and not _candidato_de_tienda_objetivo(original):
                continue
            item = dict(original)
            url = str(item.get("url") or "").strip()
            if not es_enlace_valido_sin_error(url):
                try:
                    from core.ai_reparador import reparar_url
                    url = str(reparar_url(url, item.get("tienda", ""), str(item.get("titulo", ""))) or "").strip()
                except Exception as exc:
                    logger.debug("reparar_url no disponible: %s", exc)
                if not es_enlace_valido_sin_error(url):
                    continue
            titulo = limpiar_titulo_producto(item.get("titulo") or item.get("name") or item.get("nombre"), url)
            if not titulo:
                continue
            item["titulo"], item["url"] = titulo, url

            clave = history_key(item)
            entrada = historial.get(clave) if isinstance(historial.get(clave), dict) else None
            actual, referencia, descuento = calcular_datos(item, entrada)
            if actual <= 0:
                continue
            declarado = descuento_declarado_telegram(item) if es_telegram else 0
            if declarado:
                descuento = declarado
            item["precio_actual"] = actual
            item["precio_anterior"] = referencia or None
            item["descuento"] = descuento
            item["descuento_verificado"] = bool(referencia and referencia > actual)

            publicable = MIN_DESCUENTO <= descuento <= MAX_DESCUENTO
            precio_extremo = None
            # Evita repetir una misma alerta entre ciclos para todas las fuentes,
            # incluyendo Telegram. Solo vuelve a alertar si el precio cae al menos 3%.
            previo = (entrada or {}).get("precio_alertado")
            if publicable and previo is not None and actual >= _num(previo) * 0.97:
                publicable = False

            # Las validaciones comerciales adicionales se mantienen solo para fuentes no Telegram.
            if not es_telegram:
                if 0 < actual <= 10:
                    try:
                        verificacion_extremo = analizar_precio_extremo(item)
                        if verificacion_extremo.get("publicable_como_extremo"):
                            precio_extremo = verificacion_extremo
                            publicable = True
                    except Exception as exc:
                        logger.debug("No se pudo verificar precio extremo (%s).", type(exc).__name__)
                if publicable and str(item.get("tipo_fuente") or "").upper() != "FISICA" and not es_enlace_producto_directo(url):
                    publicable = False
            if publicable and 95 <= descuento <= 99:
                # Telegram requiere consultar el registro central de forma explícita:
                # su historial local puede ser efímero entre ejecuciones de Actions.
                # Un resultado True bloquea el reenvío; None conserva el fallback local.
                if es_telegram:
                    central = supabase_history.ya_enviada(clave)
                    if central is True:
                        logger.info("[BOMBA OMITIDA] Ya registrada en Supabase: %s", clave)
                        publicable = False
                    elif not es_enlace_valido_sin_error(url):
                        publicable = False
                elif not validar_y_filtrar_bomba(item, historial):
                    publicable = False

            if publicable:
                if precio_extremo:
                    puntuacion = 100 if precio_extremo.get("nivel") == "ULTRA_EXTREMO" else 90
                    evaluacion = {"puntuacion": puntuacion, "indicadores": ["precio_extremo_verificado"], "condiciones": []}
                else:
                    try:
                        evaluacion = evaluate_product(item)
                    except Exception as exc:
                        logger.warning("evaluate_product falló para %s: %s", url, exc)
                        evaluacion = {"puntuacion": descuento, "indicadores": []}
                alertas.append({
                    "clave": clave, "item": item, "actual": actual, "referencia": referencia,
                    "descuento": descuento, "evaluacion": evaluacion, "precio_extremo": precio_extremo,
                    "bomba": DESCUENTO_BOMBA <= descuento <= MAX_DESCUENTO and (referencia > actual or es_telegram),
                })
            _actualizar_historial(historial, clave, item, actual, referencia, ahora)
        except Exception as exc:
            logger.warning("Candidato descartado por error (%s): %s", type(exc).__name__, exc)

    alertas.sort(key=lambda a: (
        90 <= int(a.get("descuento") or 0) <= 99,
        95 <= int(a.get("descuento") or 0) <= 99,
        int(a["item"].get("prioridad_fuente") or 0),
        (a["evaluacion"] or {}).get("puntuacion", 0),
        a["descuento"],
    ), reverse=True)
    return alertas

def enviar_alertas(alertas: list, historial: dict) -> tuple[int, int]:
    """Envía alertas y persiste inmediatamente las bombas confirmadas por Telegram."""
    enviadas = 0
    fallidas = 0
    limite = alertas[:MAX_ALERTAS_POR_CICLO]
    sticker = os.environ.get("TELEGRAM_STICKER_LIQUIDACION", "").strip() or None

    for indice, alerta in enumerate(limite):
        item = alerta.get("item") or {}
        descuento = _num(alerta.get("descuento"))
        es_bomba_prioritaria = 95 <= descuento <= 99
        clave = history_key(item)

        if not isinstance(clave, str) or not clave.strip():
            logger.error("[ALERTA OMITIDA] history_key(item) vacío.")
            fallidas += 1
            continue

        try:
            ok = enviar_telegram(
                formatear_alerta(alerta),
                imagen=item.get("imagen"),
                sticker_id=sticker if 95 <= descuento <= 99 else None,
            )
        except RuntimeError as exc:
            logger.error("Telegram no está listo: %s", exc)
            fallidas += len(limite) - indice
            break
        except Exception as exc:
            logger.exception("Error inesperado enviando alerta (%s).", type(exc).__name__)
            fallidas += 1
            continue

        if not ok:
            logger.error("[ALERTA FALLIDA] Telegram no confirmó el envío; llave=%s", clave)
            fallidas += len(limite) - indice
            break

        enviadas += 1
        ahora = datetime.now(timezone.utc).isoformat()
        entrada = historial.get(clave)
        if not isinstance(entrada, dict):
            entrada = {}
            historial[clave] = entrada

        entrada.update({
            "tienda": item.get("tienda"),
            "titulo": item.get("titulo"),
            "url": item.get("url"),
            "precio_actual": alerta.get("actual"),
            "precio_alertado": alerta.get("actual"),
            "ultima_actualizacion": ahora,
        })

        if es_bomba_prioritaria:
            # Guardar JSON de forma síncrona inmediatamente después de Telegram.
            try:
                guardar_historial(historial)
            except OSError as exc:
                logger.error(
                    "[BOMBA] Telegram confirmó el envío, pero falló el JSON local (%s).",
                    type(exc).__name__,
                )

            # Registrar con la misma clave canónica que usa la deduplicación.
            try:
                registrada = supabase_history.registrar_envio(
                    clave, item, int(round(descuento))
                )
                if not registrada and supabase_history.habilitado():
                    logger.error(
                        "[BOMBA] Enviada a Telegram, pero Supabase no confirmó el registro. llave=%s",
                        clave,
                    )
            except Exception as exc:
                logger.error(
                    "[BOMBA] Error guardando en Supabase (%s). llave=%s",
                    type(exc).__name__,
                    clave,
                )

    pendientes = max(0, len(alertas) - len(limite))
    if pendientes:
        logger.info("%d alertas quedan pendientes; límite=%d.", pendientes, MAX_ALERTAS_POR_CICLO)
    logger.info("Despacho Telegram finalizado: enviadas=%d fallidas=%d.", enviadas, fallidas)
    return enviadas, fallidas


def _historial_ilegible() -> bool:
    """True si el archivo existe con contenido pero no se pudo leer (para no sobrescribirlo)."""
    try:
        return os.path.exists(HISTORIAL_FILE) and os.path.getsize(HISTORIAL_FILE) > 2 and not cargar_historial()
    except OSError:
        return True


def main() -> int:
    faltantes = [v for v in VARIABLES_OPCIONALES if not os.environ.get(v, "").strip()]
    if faltantes:
        logger.info(
            "Variables opcionales no configuradas (se usan alternativas/fuentes omitidas): %s", ", ".join(faltantes)
        )
    if _historial_ilegible():
        logger.error("%s existe pero no se pudo leer; se aborta para NO sobrescribir el historial.", HISTORIAL_FILE)
        return 1

    historial = cargar_historial()
    try:
        from core.scraper_base import ScraperApiManager
        ScraperApiManager().reset_cycle()
    except Exception as exc:
        logger.warning("No se pudo reiniciar el contador de proxy (%s).", type(exc).__name__)
    candidatos = ejecutar_orquestacion_paralela()
    reporte = ULTIMO_REPORTE_FUENTES
    try:
        with open(MONITOR_HEALTH_FILE, "w", encoding="utf-8") as fh:
            json.dump(reporte, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("No se pudo escribir %s: %s", MONITOR_HEALTH_FILE, exc)

    alertas = procesar_candidatos(candidatos, historial)
    enviadas, fallidas = enviar_alertas(alertas, historial)
    guardar_historial(historial)

    # Grupo privado de Telegram (ej. "Walmart Oferstock NACIONAL"): fuente
    # aparte e intencionalmente SIN las reglas del resto del pipeline, solo
    # el 50%-99% que el propio mensaje reporta. Ver scrapers/telegram_grupo_privado.py.
    try:
        from scrapers.telegram_grupo_privado import revisar_grupo_privado

        enviadas_grupo, candidatos_grupo = revisar_grupo_privado(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
        if candidatos_grupo:
            logger.info(
                "Grupo privado Telegram: %d candidatos con 50-99%%, %d enviadas.",
                candidatos_grupo,
                enviadas_grupo,
            )
    except Exception as exc:
        logger.warning("Grupo privado Telegram: error (%s), se omite este ciclo.", exc)

    estados = [f["estado"] for f in reporte.get("fuentes", [])]
    con_fallo = [f["fuente"] for f in reporte.get("fuentes", []) if f["estado"] not in ESTADOS_SIN_FALLO]
    logger.info(
        "Resumen: %d candidatos, %d alertas, %d enviadas, %d fallidas, %d fuentes con incidencia.",
        len(candidatos),
        len(alertas),
        enviadas,
        fallidas,
        len(con_fallo),
    )
    if con_fallo:
        print(f"::warning::Fuentes con incidencia (el resto continuó): {', '.join(con_fallo)}")

    codigo = 0
    if fallidas:
        print("::error::Telegram no confirmó la entrega de algunas alertas.")
        codigo = 1
    if estados and not any(e in ESTADOS_RESPONDIO for e in estados):
        print("::error::Ninguna fuente respondió correctamente en este ciclo (caída total).")
        codigo = 1
    return codigo


if __name__ == "__main__":
    sys.exit(main())
