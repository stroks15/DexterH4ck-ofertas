from __future__ import annotations
import os, re
PATTERNS=(r"recaptcha",r"hcaptcha",r"cf-turnstile",r"turnstile",r"funcaptcha",r"arkoselabs")
def detectar(texto: str) -> str|None:
    body=(texto or "")[:200000].lower()
    return next((p for p in ("recaptcha","hcaptcha","turnstile","funcaptcha") if re.search(dict(zip(("recaptcha","hcaptcha","turnstile","funcaptcha"),PATTERNS)).get(p,"$^"),body)),None)
def resolver(tipo: str, url: str, sitekey: str="") -> dict:
    """No automatiza desafíos: terceros de resolución no se usan para eludir controles."""
    if os.getenv("CAPTCHA_PROVIDER") and os.getenv("CAPTCHA_API_KEY"):
        return {"resolved":False,"state":"captcha_required","provider_configured":True,"reason":"La resolución automática de desafíos se deshabilitó por seguridad."}
    return {"resolved":False,"state":"captcha_required","provider_configured":False}
