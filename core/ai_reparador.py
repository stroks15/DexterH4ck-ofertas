import json
import os
import re
from urllib.parse import urlparse
import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash").strip()
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
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
    if not GEMINI_API_KEY:
        return {}
    try:
        response = requests.post(
            GEMINI_ENDPOINT,
            headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
            json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0,"responseMimeType":"application/json","maxOutputTokens":700}},
            timeout=20,
        )
        if not response.ok:
            print(f"Gemini: HTTP {response.status_code}")
            return {}
        data = response.json()
        text = "".join(part.get("text","") for candidate in data.get("candidates",[]) for part in candidate.get("content",{}).get("parts",[]))
        return _extract_json(text)
    except (requests.RequestException, ValueError) as error:
        print(f"Gemini: error {error}")
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
