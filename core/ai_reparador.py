import json
import os
import re
import time
from urllib.parse import urlparse
import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_MAX_CALLS = int(os.environ.get("GEMINI_MAX_CALLS", "12"))
GEMINI_MIN_DELAY = float(os.environ.get("GEMINI_MIN_DELAY", "2.0"))
_gemini_calls = 0
_gemini_last_call = 0.0
_gemini_disabled_until = 0.0
_gemini_cache = {}
ALLOWED_HOSTS = {"soriana.com","www.soriana.com","coppel.com","www.coppel.com","suburbia.com.mx","www.suburbia.com.mx","oferstock.com.mx","www.oferstock.com.mx","walmart.com.mx","www.walmart.com.mx","bodegaaurrera.com.mx","www.bodegaaurrera.com.mx","chedraui.com.mx","www.chedraui.com.mx","liverpool.com.mx","www.liverpool.com.mx","amazon.com.mx","www.amazon.com.mx","mercadolibre.com.mx","www.mercadolibre.com.mx","meli.la","amzn.to"}

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

def _gemini(prompt):
    global _gemini_calls, _gemini_last_call, _gemini_disabled_until
    if not GEMINI_API_KEY:
        return {}
    now = time.monotonic()
    if now < _gemini_disabled_until:
        return {}
    if _gemini_calls >= GEMINI_MAX_CALLS:
        return {}
    cache_key = prompt.strip()
    if cache_key in _gemini_cache:
        return _gemini_cache[cache_key]
    wait = GEMINI_MIN_DELAY - (now - _gemini_last_call)
    if wait > 0:
        time.sleep(wait)
    _gemini_last_call = time.monotonic()
    try:
        _gemini_calls += 1
        response = requests.post(
            GEMINI_ENDPOINT,
            headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
            json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json","maxOutputTokens":700}},
            timeout=20,
        )
        if response.status_code == 429:
            retry_after = 30
            try:
                retry_after = int(response.headers.get("Retry-After", "30"))
            except ValueError:
                pass
            _gemini_disabled_until = time.monotonic() + min(max(retry_after, 30), 300)
            print(f"Gemini: HTTP 429; pausando IA durante {_gemini_disabled_until - time.monotonic():.0f}s y continuando sin IA.")
            return {}
        if not response.ok:
            print(f"Gemini: HTTP {response.status_code}; continuando sin IA para este candidato.")
            return {}
        data = response.json()
        text = "".join(part.get("text","") for candidate in data.get("candidates",[]) for part in candidate.get("content",{}).get("parts",[]))
        result = _extract_json(text)
        _gemini_cache[cache_key] = result
        return result
    except (requests.RequestException, ValueError) as error:
        print(f"Gemini: error {error}; continuando sin IA.")
        return {}

def reparar_url(url, tienda="", contexto=""):
    if not url or not GEMINI_API_KEY:
        return url
    prompt = f"""Eres un reparador de enlaces para un monitor de ofertas de México.
Devuelve SOLO JSON: {{"url":"URL corregida o la original","confidence":0,"reason":"breve"}}
NO inventes una página nueva. Solo corrige sintaxis, escapes HTML, parámetros basura,
dominios equivalentes y rutas obvias de la misma tienda. Conserva el producto concreto.
Tienda: {tienda}
URL: {url}
Contexto: {contexto[:800]}"""
    result = _gemini(prompt)
    propuesta = str(result.get("url") or "").strip()
    try:
        confidence = float(result.get("confidence",0))
    except (TypeError,ValueError):
        confidence = 0
    return propuesta if propuesta and confidence >= 0.65 and _host_allowed(propuesta) else url

def analizar_publicacion(texto, url="", tienda=""):
    if not GEMINI_API_KEY or not texto:
        return {}
    prompt = f"""Analiza esta publicación de ofertas de México. No inventes datos.
Devuelve SOLO JSON con: titulo, tienda, precio_actual, precio_anterior, descuento,
liquidacion, marca, categoria, url, confianza.
Precio actual = precio final explícito. Precio anterior solo si está explícito.
Cupones/bonificaciones no son precio anterior. Calcula descuento solo con ambos precios.
Publicación:
{texto[:5000]}
URL recibida: {url}
Tienda detectada: {tienda}"""
    return _gemini(prompt)
