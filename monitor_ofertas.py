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
    "Walmart MX", "Bodega Aurrera", "Chedraui", "Soriana",
    "Liverpool", "Amazon MX", "Mercado Libre MX", "Coppel",
    "Suburbia", "Oferstock",
)

def cargar_historial():
    if not os.path.exists(HISTORIAL_FILE):
        return {}
    try:
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as archivo:
            data = json.load(archivo)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}

def guardar_historial(historial):
    if len(historial) > MAX_HISTORIAL:
        historial = dict(sorted(
            historial.items(),
            key=lambda par: (par[1].get("ultima_actualizacion", "") if isinstance(par[1], dict) else ""),
            reverse=True,
        )[:MAX_HISTORIAL])
    temporal = f"{HISTORIAL_FILE}.tmp"
    with open(temporal, "w", encoding="utf-8") as archivo:
        json.dump(historial, archivo, ensure_ascii=False, indent=2)
    os.replace(temporal, HISTORIAL_FILE)

def limpiar_titulo_producto(titulo, url=""):
    texto = html.unescape(str(titulo or ""))
    texto = re.sub(r"\s+", " ", texto).strip(" \t\r\n-–—|·")
    texto = re.split(
        r"\b(?:precio\s+(?:actual|final|de\s+oferta)|antes|ahorra|hasta\s+\d+\s+mensualidades?|mensualidades?\s+fijas?|precio\s+anterior|precio\s+regular)\b",
        texto, maxsplit=1, flags=re.I,
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
    if any(x in path for x in ("/search", "/buscar", "/ofertas", "/oferta", "/catalogo", "/marcas", "/home", "/social/")):
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
    """Valida que el link del producto no contenga errores estructurales."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if not parsed.scheme in ["http", "https"] or not parsed.netloc:
        return False
    path_lower = parsed.path.lower()
    if any(err in path_lower for err in ["null", "undefined", "{productid}"]):
        return False
    return True

def validar_y_filtrar_bomba(item: dict, historial_local: dict) -> bool:
    """
    Regla estricta del 95% al 99%: Se mandan una única vez si no existen 
    en el historial y el link del producto es completamente válido.
    """
    precio_actual = float(item.get("precio_actual") or item.get("price") or 0)
    precio_anterior = item.get("precio_anterior")
    descuento = item.get("descuento", 0)
    
    if not descuento and precio_anterior and float(precio_anterior) > precio_actual:
        descuento = round((1 - precio_actual / float(precio_anterior)) * 100)

    if 95 <= descuento <= 99:
        contexto_producto = canonical_product_identifier(item.get("tienda", ""), item.get("url", ""))
        identificador_producto = (
            item.get("id") or contexto_producto.get("product_id") or contexto_producto.get("url") or item.get("url", "")
        )
        tienda_canonica = canonical_store(item.get("tienda", ""))
        clave_historial = f"{tienda_canonica}_{identificador_producto}"
        
        entrada_actual = historial_local.get(history_key(item))
        ya_alertada = isinstance(entrada_actual, dict) and entrada_actual.get("precio_alertado") is not None
        if clave_historial in historial_local or ya_alertada:
            logger.info(f"[BOMBA OMITIDA] Ya fue enviada previamente: {clave_historial}")
            return False
            
        if not es_enlace_valido_sin_error(item.get("url", "")):
            logger.warning(f"[BOMBA RECHAZADA] Enlace con errores: {item.get('url')}")
            return False
            
        logger.info(f"[💣 BOMBA EMITIDA] Nueva liquidación extrema única del {descuento}%")
        return True
    return True

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

DESCUENTO_BOMBA = 90          # 90–99 % con referencia válida => alerta prioritaria 💣
MAX_ALERTAS_POR_CICLO = int(os.environ.get("MAX_ALERTAS_POR_CICLO", "25") or 25)
FUENTES_TIMEOUT_TOTAL = float(os.environ.get("FUENTES_TIMEOUT_TOTAL", "540") or 540)
FUENTES_CONCURRENCIA = 6
MONITOR_HEALTH_FILE = "monitor_health.json"

# Estados de fuente que NO son un fallo (no hubo error real de acceso).
ESTADOS_SIN_FALLO = {"ok", "empty", "not_configured", "skipped_preflight"}
# Estados que indican que la fuente sí respondió con datos o vacía (para detectar caída total).
ESTADOS_RESPONDIO = {"ok", "empty"}

VARIABLES_OPCIONALES = (
    "WALMART_GRAPHQL_URL", "WALMART_STORE_ID", "WALMART_GRAPHQL_QUERY",
    "BODEGA_GRAPHQL_URL", "BODEGA_STORE_ID", "BODEGA_GRAPHQL_QUERY",
    "CHEDRAUI_VTEX_ENDPOINT", "SORIANA_API_ENDPOINT", "COPPEL_API_ENDPOINT",
    "SUBURBIA_API_ENDPOINT", "AMAZON_API_ENDPOINT", "ML_QUERIES",
    "MERCADOLIBRE_ACCESS_TOKEN",
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


def calcular_datos(item: dict, entrada_historial: dict | None = None):
    """Devuelve (precio_actual, precio_referencia, descuento).

    La referencia es conservadora: se usa el precio anterior explícito de la oferta
    si es válido; solo si no existe se recurre al precio máximo del historial.
    """
    actual = _num(item.get("precio_actual", item.get("price")))
    anterior = _num(item.get("precio_anterior", item.get("previous_price")))
    maximo = _num((entrada_historial or {}).get("precio_maximo"))
    if actual > 0 and anterior > actual:
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
            session.post(_telegram_api("sendSticker"),
                         data={"chat_id": TELEGRAM_CHAT_ID, "sticker": sticker_id}, timeout=15)
        except requests.RequestException as exc:
            logger.warning("Telegram sticker error: %s", type(exc).__name__)

    metodo = "sendPhoto" if imagen else "sendMessage"
    for intento in range(3):
        try:
            if metodo == "sendPhoto":
                response = session.post(_telegram_api(metodo), data={
                    "chat_id": TELEGRAM_CHAT_ID, "photo": imagen, "caption": texto[:1000], "parse_mode": "HTML"}, timeout=25)
            else:
                response = session.post(_telegram_api(metodo), data={
                    "chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML",
                    "disable_web_page_preview": False}, timeout=20)
            _ultimo_envio_telegram = time.monotonic()
            if response.status_code == 200:
                return True
            if response.status_code == 429:
                try:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 5))
                except (ValueError, TypeError, AttributeError):
                    retry_after = 5
                time.sleep(min(max(retry_after, 1), 60) + 0.5)
                continue
            logger.error("Telegram HTTP %s en %s", response.status_code, metodo)
            if metodo == "sendPhoto":      # la foto falló: se reintenta como texto
                metodo, imagen = "sendMessage", None
                continue
            if response.status_code in (400, 401, 403, 404):
                return False               # credencial/chat inválidos: reintentar no ayuda
        except requests.RequestException as exc:
            logger.warning("Telegram error de red (%s) en intento %s", type(exc).__name__, intento + 1)
            if metodo == "sendPhoto":
                metodo, imagen = "sendMessage", None
            time.sleep(1.5)
    return False


def formatear_alerta(alerta: dict) -> str:
    item = alerta["item"]
    bomba = alerta["bomba"]
    tipo = "VERDE" if es_enlace_producto_directo(item.get("url")) else "ROJO"
    marca = "💣 <b>LIQUIDACIÓN EXTREMA</b>\n" if bomba else ""
    lineas = [
        f"{marca}{formato_alerta_tipo(tipo)} <b>{html.escape(str(item.get('titulo') or ''))}</b>",
        f"🏪 {html.escape(str(item.get('tienda') or 'Tienda'))}",
        f"💰 ${alerta['actual']:,.2f} MXN (antes ${alerta['referencia']:,.2f})",
        f"📉 {alerta['descuento']}% de descuento",
    ]
    ev = alerta.get("evaluacion") or {}
    if ev.get("puntuacion") is not None:
        extra = f" · {', '.join(ev.get('indicadores', [])[:4])}" if ev.get("indicadores") else ""
        lineas.append(f"⭐ Puntuación {ev['puntuacion']}/100{html.escape(extra)}")
    if ev.get("condiciones"):
        lineas.append("⚠️ Condiciones: " + html.escape(", ".join(map(str, ev["condiciones"]))))
    lineas.append(f"🔗 {item.get('url', '')}")
    return "\n".join(lineas)


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
    sesion.headers.update({
        "User-Agent": HONEST_USER_AGENT,
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
    })
    return sesion


def _tiendas_bloqueadas() -> set:
    """Tiendas que no pasaron el preflight (PREFLIGHT_BLOCKED_STORES), en forma canónica."""
    bruto = os.environ.get("PREFLIGHT_BLOCKED_STORES", "")
    return {canonical_store(t) for t in bruto.split(",") if t.strip()}


def _estado_tiendas_mexico():
    from scrapers import tiendas_mexico
    estados = list(tiendas_mexico.LAST_STATUS.values())
    if not estados or any(e == "ok" for e in estados):
        return {"state": "ok"}
    return {"state": estados[0]}


def _estado_modulo(modulo: str):
    return lambda: dict(getattr(importlib.import_module(modulo), "LAST_STATUS", {}) or {})


def definir_fuentes(sesion: requests.Session | None = None) -> list:
    sesion = sesion or _crear_sesion()
    fuentes: list[Fuente] = []

    # API-first: un adaptador aislado por tienda
    for scraper in ApiStoresScraper().scrapers:
        fuentes.append(Fuente(f"api:{scraper.store}", {canonical_store(scraper.store)},
                              scraper.discover, lambda s=scraper: s.last_status))

    # Legacy/browser en paralelo
    fuentes.append(Fuente("legacy:tiendas_mexico", {"amazon", "soriana", "liverpool"},
                          _lazy("scrapers.tiendas_mexico", "buscar_todas"), _estado_tiendas_mexico))
    fuentes.append(Fuente("legacy:liquidaciones_oficiales", set(),
                          _lazy("scrapers.liquidaciones_oficiales", "buscar_liquidaciones_oficiales", sesion)))
    fuentes.append(Fuente("legacy:tiendas_fisicas", set(),
                          _lazy("scrapers.tiendas_fisicas", "buscar_tiendas_fisicas", sesion)))
    fuentes.append(Fuente("comunidad:telegram", set(),
                          _lazy("scrapers.telegram_ofertas", "buscar_telegram", sesion)))
    fuentes.append(Fuente("comunidad:whatsapp", set(),
                          _lazy("scrapers.whatsapp_channels", "buscar_whatsapp"), _estado_modulo("scrapers.whatsapp_channels")))
    fuentes.append(Fuente("comunidad:feed", set(),
                          _lazy("scrapers.feeds_comunidad_api", "parsear_feed_comunidad_espejo")))
    fuentes.append(Fuente("comunidad:web", set(),
                          _lazy("scrapers.comunidades_web", "buscar_comunidades_web")))
    fuentes.append(Fuente("liquidazona:walmart", {canonical_store("Walmart MX")},
                          _lazy("scrapers.liquidazona", "buscar_liquidazona_walmart", sesion)))
    if os.environ.get("NETWORK_BROWSER_ENABLED", "false").strip().lower() in ("1", "true", "yes", "si"):
        fuentes.append(Fuente("browser:network_json", set(),
                              _lazy("scrapers.browser_network", "buscar_network_browser")))
    return fuentes


def _ejecutar_fuente(fuente: Fuente, salida: dict, semaforo: threading.Semaphore) -> None:
    inicio = time.monotonic()
    registro = {"fuente": fuente.nombre, "estado": "error", "candidatos": 0, "segundos": 0.0}
    with semaforo:
        _TLS.http = []
        try:
            items = fuente.funcion() or []
            if not isinstance(items, list):
                registro.update(estado="invalid_response", error=f"tipo inesperado: {type(items).__name__}")
            else:
                fuente.resultado = [i for i in items if isinstance(i, dict)]
                registro["candidatos"] = len(fuente.resultado)
                estado = {}
                if fuente.estado_fn:
                    try:
                        estado = fuente.estado_fn() or {}
                    except Exception as exc:  # leer el estado jamás tumba la fuente
                        estado = {}
                        logger.debug("estado_fn de %s falló: %s", fuente.nombre, exc)
                declarado = str(estado.get("state") or "")
                if declarado and declarado not in ("not_run", "ok"):
                    registro["estado"] = declarado
                    if estado.get("http"):
                        registro["http"] = estado["http"]
                elif fuente.resultado:
                    registro["estado"] = "ok"
                else:
                    # Sin resultados: ¿realmente vacía o todas sus peticiones fueron rechazadas?
                    registro["estado"] = estado_desde_http(_TLS.http) or "empty"
        except Exception as exc:  # aislamiento: un fallo no afecta a otras fuentes
            registro.update(estado="error", error=f"{type(exc).__name__}: {exc}"[:300])
            logger.error("[%s] fallo aislado: %s", fuente.nombre, registro["error"])
    registro["segundos"] = round(time.monotonic() - inicio, 1)
    salida[fuente.nombre] = registro


def ejecutar_orquestacion_paralela(contexto_ficticio=None) -> list:
    """Orquestación paralela: cada fuente corre aislada, con timeout y estado real reportado."""
    global ULTIMO_REPORTE_FUENTES
    logger.info("Iniciando despacho multihilo de scrapers de liquidación...")
    _instalar_registro_http()
    fuentes = definir_fuentes()
    bloqueadas = _tiendas_bloqueadas()
    salida: dict = {}
    hilos: list[tuple[Fuente, threading.Thread]] = []
    semaforo = threading.Semaphore(FUENTES_CONCURRENCIA)

    for fuente in fuentes:
        if fuente.tiendas and fuente.tiendas <= bloqueadas:
            salida[fuente.nombre] = {"fuente": fuente.nombre, "estado": "skipped_preflight",
                                     "candidatos": 0, "segundos": 0.0}
            logger.warning("[%s] omitida: la tienda no pasó el preflight (las demás continúan).", fuente.nombre)
            continue
        hilo = threading.Thread(target=_ejecutar_fuente, args=(fuente, salida, semaforo),
                                name=f"src-{fuente.nombre}", daemon=True)
        hilo.start()
        hilos.append((fuente, hilo))

    limite = time.monotonic() + FUENTES_TIMEOUT_TOTAL
    for fuente, hilo in hilos:
        hilo.join(max(0.0, limite - time.monotonic()))
        if hilo.is_alive():
            salida.setdefault(fuente.nombre, {"fuente": fuente.nombre, "estado": "timeout",
                                              "candidatos": 0, "segundos": FUENTES_TIMEOUT_TOTAL})
            logger.error("[%s] timeout: se abandona esta fuente y el monitor continúa.", fuente.nombre)

    candidatos: list = []
    for fuente in fuentes:
        if salida.get(fuente.nombre, {}).get("estado") != "timeout":
            candidatos.extend(fuente.resultado)

    ULTIMO_REPORTE_FUENTES = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preflight_bloqueadas": sorted(bloqueadas),
        "fuentes": [salida[f.nombre] for f in fuentes if f.nombre in salida],
    }
    for fila in ULTIMO_REPORTE_FUENTES["fuentes"]:
        nivel = logging.INFO if fila["estado"] in ESTADOS_SIN_FALLO else logging.WARNING
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
    nueva.update({
        "tienda": item.get("tienda"), "titulo": item.get("titulo"), "url": item.get("url"),
        "precio_actual": actual, "precio_maximo": maximo, "ultima_actualizacion": ahora,
    })
    nueva.setdefault("precio_alertado", None)
    historial[clave] = nueva
    return nueva


def procesar_candidatos(candidatos: list, historial: dict) -> list:
    """Filtra, puntúa y devuelve las alertas ordenadas por prioridad. Actualiza el historial."""
    ahora = datetime.now(timezone.utc).isoformat()
    unicos, duplicados = deduplicate_candidates([c for c in candidatos if isinstance(c, dict)])
    logger.info("Candidatos: %d únicos (%d duplicados descartados).", len(unicos), duplicados)
    alertas = []

    for original in unicos:
        try:
            if not _candidato_de_tienda_objetivo(original):
                continue
            item = dict(original)
            url = str(item.get("url") or "").strip()
            if not es_enlace_valido_sin_error(url):
                try:
                    from core.ai_reparador import reparar_url
                    url = str(reparar_url(url, item.get("tienda", ""), str(item.get("titulo", ""))) or "").strip()
                except Exception as exc:  # sin IA se continúa con la URL original
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
            item["precio_actual"] = actual
            item["precio_anterior"] = referencia or None
            item["descuento"] = descuento

            publicable = MIN_DESCUENTO <= descuento <= MAX_DESCUENTO
            if publicable and str(item.get("tipo_fuente") or "").upper() != "FISICA" \
                    and not es_enlace_producto_directo(url):
                publicable = False
            if publicable and 95 <= descuento <= 99 and not validar_y_filtrar_bomba(item, historial):
                publicable = False
            previo = (entrada or {}).get("precio_alertado")
            if publicable and previo is not None and actual >= _num(previo) * 0.97:
                publicable = False          # ya alertado a este precio (o menor)

            if publicable:
                try:
                    evaluacion = evaluate_product(item)
                except Exception as exc:
                    logger.warning("evaluate_product falló para %s: %s", url, exc)
                    evaluacion = {"puntuacion": descuento, "indicadores": []}
                alertas.append({
                    "clave": clave, "item": item, "actual": actual, "referencia": referencia,
                    "descuento": descuento, "evaluacion": evaluacion,
                    "bomba": DESCUENTO_BOMBA <= descuento <= MAX_DESCUENTO and referencia > actual,
                })
            _actualizar_historial(historial, clave, item, actual, referencia, ahora)
        except Exception as exc:  # un candidato defectuoso no detiene el ciclo
            logger.warning("Candidato descartado por error (%s): %s", type(exc).__name__, exc)

    alertas.sort(key=lambda a: (a["bomba"], (a["evaluacion"] or {}).get("puntuacion", 0), a["descuento"]), reverse=True)
    return alertas


def enviar_alertas(alertas: list, historial: dict) -> tuple[int, int]:
    enviadas = fallidas = 0
    sticker = os.environ.get("TELEGRAM_STICKER_LIQUIDACION", "").strip() or None
    for alerta in alertas[:MAX_ALERTAS_POR_CICLO]:
        try:
            ok = enviar_telegram(formatear_alerta(alerta), imagen=alerta["item"].get("imagen"),
                                 sticker_id=sticker if alerta["bomba"] else None)
        except RuntimeError as exc:
            logger.error("%s", exc)
            return enviadas, fallidas + len(alertas[:MAX_ALERTAS_POR_CICLO]) - enviadas - fallidas
        if ok:
            enviadas += 1
            entrada = historial.get(alerta["clave"])
            if isinstance(entrada, dict):
                entrada["precio_alertado"] = alerta["actual"]
                entrada["ultima_actualizacion"] = datetime.now(timezone.utc).isoformat()
        else:
            fallidas += 1
    pendientes = max(0, len(alertas) - MAX_ALERTAS_POR_CICLO)
    if pendientes:
        logger.info("%d alertas quedan para el siguiente ciclo (límite %d).", pendientes, MAX_ALERTAS_POR_CICLO)
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
        logger.info("Variables opcionales no configuradas (se usan alternativas/fuentes omitidas): %s", ", ".join(faltantes))
    if _historial_ilegible():
        logger.error("%s existe pero no se pudo leer; se aborta para NO sobrescribir el historial.", HISTORIAL_FILE)
        return 1

    historial = cargar_historial()
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

    estados = [f["estado"] for f in reporte.get("fuentes", [])]
    con_fallo = [f["fuente"] for f in reporte.get("fuentes", []) if f["estado"] not in ESTADOS_SIN_FALLO]
    logger.info("Resumen: %d candidatos, %d alertas, %d enviadas, %d fallidas, %d fuentes con incidencia.",
                len(candidatos), len(alertas), enviadas, fallidas, len(con_fallo))
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
