# perimetro/captcha.py
# Detección de CAPTCHA y resolución opcional vía proveedor externo.
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

TIPOS = {
    "recaptcha": ("g-recaptcha", "grecaptcha", "recaptcha/api.js", "data-sitekey"),
    "hcaptcha": ("h-captcha", "hcaptcha.com", "data-hcaptcha"),
    "turnstile": ("cf-turnstile", "challenges.cloudflare.com/turnstile"),
    "funcaptcha": ("funcaptcha", "arkoselabs", "enforcement"),
}

SITEKEY = re.compile(r"data-sitekey=[\"']([A-Za-z0-9_\-]{10,})[\"']", re.I)


def detectar(cuerpo: str) -> str:
    muestra = (cuerpo or "")[:60000].lower()
    for tipo, pistas in TIPOS.items():
        if any(pista in muestra for pista in pistas):
            return tipo
    return ""


def sitekey(cuerpo: str) -> str:
    encontrado = SITEKEY.search(cuerpo or "")
    return encontrado.group(1) if encontrado else ""


def configurado() -> bool:
    return bool(os.environ.get("CAPTCHA_PROVIDER", "").strip() and os.environ.get("CAPTCHA_API_KEY", "").strip())


def _post(url: str, datos: dict) -> dict:
    cuerpo = urllib.parse.urlencode(datos).encode()
    peticion = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(peticion, timeout=30) as respuesta:
        return json.loads(respuesta.read().decode("utf-8", "replace") or "{}")


def _crear(proveedor: str, clave: str, tipo: str, pagina: str, llave: str) -> str:
    if proveedor == "2captcha":
        datos = {"key": clave, "method": "userrecaptcha", "googlekey": llave, "pageurl": pagina, "json": 1}
        if tipo == "hcaptcha":
            datos = {"key": clave, "method": "hcaptcha", "sitekey": llave, "pageurl": pagina, "json": 1}
        elif tipo == "turnstile":
            datos = {"key": clave, "method": "turnstile", "sitekey": llave, "pageurl": pagina, "json": 1}
        return str(_post("https://2captcha.com/in.php", datos).get("request", ""))
    if proveedor in ("capsolver", "capmonster"):
        tipo_cap = {"recaptcha": "ReCaptchaV2TaskProxyLess", "hcaptcha": "HCaptchaTaskProxyLess", "turnstile": "AntiTurnstileTaskProxyLess"}.get(tipo, "ReCaptchaV2TaskProxyLess")
        base = "https://api.capsolver.com" if proveedor == "capsolver" else "https://api.capmonster.cloud"
        cuerpo = {"clientKey": clave, "task": {"type": tipo_cap, "websiteURL": pagina, "websiteKey": llave}}
        peticion = urllib.request.Request(base + "/createTask", data=json.dumps(cuerpo).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(peticion, timeout=30) as respuesta:
            return str(json.loads(respuesta.read().decode("utf-8", "replace") or "{}").get("taskId", ""))
    return ""


def _resultado(proveedor: str, clave: str, tarea: str) -> str:
    if not tarea:
        return ""
    for _ in range(28):
        time.sleep(6)
        try:
            if proveedor == "2captcha":
                token = _post("https://2captcha.com/res.php", {"key": clave, "action": "get", "id": tarea, "json": 1})
                if token.get("status") == 1:
                    return str(token.get("request", ""))
            else:
                base = "https://api.capsolver.com" if proveedor == "capsolver" else "https://api.capmonster.cloud"
                peticion = urllib.request.Request(base + "/getTaskResult", data=json.dumps({"clientKey": clave, "taskId": tarea}).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(peticion, timeout=30) as respuesta:
                    data = json.loads(respuesta.read().decode("utf-8", "replace") or "{}")
                if data.get("status") == "ready":
                    solucion = data.get("solution") or {}
                    return str(solucion.get("gRecaptchaResponse") or solucion.get("token") or "")
        except Exception:
            continue
    return ""


def resolver(cuerpo: str, pagina: str) -> dict[str, str]:
    if not configurado():
        return {}
    tipo = detectar(cuerpo)
    llave = sitekey(cuerpo)
    if not tipo or not llave:
        return {}
    proveedor = os.environ["CAPTCHA_PROVIDER"].strip().lower()
    clave = os.environ["CAPTCHA_API_KEY"].strip()
    try:
        token = _resultado(proveedor, clave, _crear(proveedor, clave, tipo, pagina, llave))
    except Exception:
        return {}
    return {"tipo": tipo, "token": token} if token else {}
