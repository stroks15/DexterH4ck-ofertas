import json
import os
import random
import re
import time
from urllib.parse import urlparse

import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_MAX_CALLS = int(os.environ.get("GEMINI_MAX_CALLS", "12"))
GEMINI_MIN_DELAY = float(os.environ.get("GEMINI_MIN_DELAY", "2.0"))

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MAX_CALLS = int(os.environ.get("GROQ_MAX_CALLS", "12"))
GROQ_MIN_DELAY = float(os.environ.get("GROQ_MIN_DELAY", "1.5"))

_gemini_calls = 0
_gemini_last_call = 0.0
_gemini_disabled_until = 0.0
_groq_calls = 0
_groq_last_call = 0.0
_groq_disabled_until = 0.0
_ai_cache = {}
_ai_unavailable_reported = False

ALLOWED_HOSTS = {
    "soriana.com", "www.soriana.com", "coppel.com", "www.coppel.com",
    "suburbia.com.mx", "www.suburbia.com.mx", "oferstock.com.mx",
    "www.oferstock.com.mx", "walmart.com.mx", "www.walmart.com.mx",
    "bodegaaurrera.com.mx", "www.bodegaaurrera.com.mx", "chedraui.com.mx",
    "www.chedraui.com.mx", "liverpool.com.mx", "www.liverpool.com.mx",
    "amazon.com.mx", "www.amazon.com.mx", "mercadolibre.com.mx",
    "www.mercadolibre.com.mx", "meli.la", "amzn.to",
}


def _host_allowed(url):
    try:
        return urlparse(url).netloc.lower().split(":")[0] in ALLOWED_HOSTS
    except Exception:
        return False


def _extract_json(text):
    try:
        return json.loads((text or "").strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text or "", re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _retry_after(response, default=2):
    try:
        value = float(response.headers.get("Retry-After", default))
        return max(0.5, min(value, 30.0))
    except (TypeError, ValueError):
        return float(default)


def _backoff(attempt, response=None):
    if response is not None and response.status_code == 429:
        base = _retry_after(response, 2)
    else:
        base = min(2 ** attempt, 12)
    return min(base + random.uniform(0.1, 0.8), 30.0)


def _gemini(prompt):
    global _gemini_calls, _gemini_last_call, _gemini_disabled_until

    if not GEMINI_API_KEY:
        return {}

    now = time.monotonic()
    if now < _gemini_disabled_until:
        return {}
    if _gemini_calls >= GEMINI_MAX_CALLS:
        return {}

    cache_key = "gemini|" + prompt.strip()
    if cache_key in _ai_cache:
        return _ai_cache[cache_key]

    wait = GEMINI_MIN_DELAY - (now - _gemini_last_call)
    if wait > 0:
        time.sleep(wait)

    for intento in range(2):
        _gemini_last_call = time.monotonic()
        try:
            _gemini_calls += 1
            response = requests.post(
                GEMINI_ENDPOINT,
                headers={
                    "x-goog-api-key": GEMINI_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "maxOutputTokens": 700,
                    },
                },
                timeout=20,
            )

            if response.ok:
                data = response.json()
                text = "".join(
                    part.get("text", "")
                    for candidate in data.get("candidates", [])
                    for part in candidate.get("content", {}).get("parts", [])
                )
                result = _extract_json(text)
                if result:
                    _ai_cache[cache_key] = result
                return result

            if response.status_code in (408, 429, 500, 502, 503, 504) and intento == 0:
                espera = _backoff(intento, response)
                print(f"Gemini: HTTP {response.status_code}; reintento en {espera:.1f}s.")
                time.sleep(espera)
                continue

            if response.status_code in (401, 403):
                print(f"Gemini: HTTP {response.status_code}; la clave fue rechazada (credencial/permisos/proyecto).")
            elif response.status_code == 429:
                _gemini_disabled_until = time.monotonic() + 30
                print("Gemini: HTTP 429; activando respaldo Groq y pausando Gemini durante 30s.")
            else:
                print(f"Gemini: HTTP {response.status_code}; activando respaldo Groq.")
            return {}

        except (requests.RequestException, ValueError) as error:
            if intento == 0:
                espera = _backoff(intento)
                print(f"Gemini: error temporal {error}; reintento en {espera:.1f}s.")
                time.sleep(espera)
                continue
            print(f"Gemini: error {error}; activando respaldo Groq.")
            return {}

    return {}


def _groq(prompt):
    global _groq_calls, _groq_last_call, _groq_disabled_until

    if not GROQ_API_KEY:
        return {}
    if time.monotonic() < _groq_disabled_until:
        return {}
    if _groq_calls >= GROQ_MAX_CALLS:
        return {}

    cache_key = "groq|" + prompt.strip()
    if cache_key in _ai_cache:
        return _ai_cache[cache_key]

    wait = GROQ_MIN_DELAY - (time.monotonic() - _groq_last_call)
    if wait > 0:
        time.sleep(wait)

    for intento in range(2):
        _groq_last_call = time.monotonic()
        try:
            _groq_calls += 1
            payload = {
                "model": GROQ_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Eres un respaldo de un monitor de ofertas de México. "
                            "No inventes precios, productos ni URLs. Devuelve JSON válido."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 700,
                "response_format": {"type": "json_object"},
            }
            response = requests.post(
                GROQ_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=20,
            )
            if response.status_code == 400:
                payload.pop("response_format", None)
                response = requests.post(
                    GROQ_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=20,
                )

            if response.ok:
                data = response.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                result = _extract_json(text)
                if result:
                    _ai_cache[cache_key] = result
                return result

            if response.status_code in (408, 429, 500, 502, 503, 504) and intento == 0:
                espera = _backoff(intento, response)
                print(f"Groq: HTTP {response.status_code}; reintento en {espera:.1f}s.")
                time.sleep(espera)
                continue

            if response.status_code in (401, 403):
                print(f"Groq: HTTP {response.status_code}; la clave fue rechazada (credencial/permisos).")
            elif response.status_code == 429:
                _groq_disabled_until = time.monotonic() + 30
                print("Groq: HTTP 429; pausando Groq durante 30s.")
            else:
                print(f"Groq: HTTP {response.status_code}; continuando sin IA. Revisa modelo/configuración de la API.")
            return {}

        except (requests.RequestException, ValueError) as error:
            if intento == 0:
                espera = _backoff(intento)
                print(f"Groq: error temporal {error}; reintento en {espera:.1f}s.")
                time.sleep(espera)
                continue
            print(f"Groq: error {error}; continuando sin IA.")
            return {}

    return {}


def _ai(prompt):
    """Prioridad estricta: Gemini -> Groq -> sin IA, con circuito cerrado."""
    global _ai_unavailable_reported
    cache_key = "final|" + prompt.strip()
    if cache_key in _ai_cache:
        return _ai_cache[cache_key]

    result = _gemini(prompt)
    if result:
        _ai_cache[cache_key] = result
        print("IA: respuesta obtenida con Gemini.")
        return result

    result = _groq(prompt)
    if result:
        _ai_cache[cache_key] = result
        print("IA: Gemini no disponible; respuesta obtenida con Groq.")
        return result

    if not _ai_unavailable_reported:
        print("IA: Gemini/Groq no disponibles; se continúa con extracción determinista. Se silencia este aviso durante el resto de la ejecución.")
        _ai_unavailable_reported = True
    return {}


def reparar_url(url, tienda="", contexto=""):
    if not url:
        return url

    prompt = f"""Eres un reparador de enlaces para un monitor de ofertas de México.
Devuelve SOLO JSON: {{"url":"URL corregida o la original","confidence":0,"reason":"breve"}}
NO inventes una página nueva. Solo corrige sintaxis, escapes HTML, parámetros basura,
dominios equivalentes y rutas obvias de la misma tienda. Conserva el producto concreto.
Si no puedes demostrar una corrección segura, devuelve exactamente la URL recibida con
confidence 0. No conviertas una página de búsqueda/categoría en una ficha de producto.
Tienda: {tienda}
URL: {url}
Contexto: {contexto[:800]}"""

    result = _ai(prompt)
    propuesta = str(result.get("url") or "").strip()
    try:
        confidence = float(result.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0

    if not propuesta or confidence < 0.65 or not _host_allowed(propuesta):
        return url
    original_host = urlparse(url).netloc.lower().split(":")[0]
    proposed_host = urlparse(propuesta).netloc.lower().split(":")[0]
    shorteners = {"meli.la", "amzn.to", "bit.ly", "tidd.ly", "link.amazon"}
    # La IA solo puede cambiar la ruta si conserva la misma tienda. Un acortador
    # puede resolverse a una tienda oficial, pero nunca a otra tienda.
    if original_host not in shorteners and proposed_host != original_host:
        return url
    if original_host in shorteners and proposed_host not in ALLOWED_HOSTS:
        return url
    return propuesta


def analizar_publicacion(texto, url="", tienda=""):
    if not texto:
        return {}

    prompt = f"""Analiza esta publicación de ofertas de México. No inventes datos.
Devuelve SOLO JSON con: titulo, tienda, precio_actual, precio_anterior, descuento,
liquidacion, marca, categoria, url, confianza.
Precio actual = precio final explícito. Precio anterior solo si está explícito.
Cupones/bonificaciones no son precio anterior. Calcula descuento solo con ambos precios.
Si la URL recibida no parece ficha directa, conserva la URL y no inventes otra.
Publicación:
{texto[:5000]}
URL recibida: {url}
Tienda detectada: {tienda}"""

    return _ai(prompt)
