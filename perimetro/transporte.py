from __future__ import annotations
import os,random,time
from urllib.parse import urlparse
import requests
from .captcha import detectar
from .ritmo import Ritmo
from .stealth import headers
from .waf import Cooldown,clasificar

class Transporte:
    def __init__(self,timeout=12,session=None):self.timeout=timeout;self.session=session or requests.Session();self.ritmo=Ritmo();self.cooldown=Cooldown()
    def pedir(self,url,params=None):
        host=urlparse(url).netloc.lower()
        if self.cooldown.active(host):return {"state":"cooldown","status":None,"body":"","url":url,"headers":{}}
        self.ritmo.adquirir(url)
        try:r=self.session.get(url,params=params,headers=headers(host),timeout=self.timeout,allow_redirects=True)
        except requests.Timeout:return {"state":"timeout","status":None,"body":"","url":url,"headers":{}}
        except requests.RequestException:return {"state":"invalid_response","status":None,"body":"","url":url,"headers":{}}
        body=r.text or ""; state=clasificar(r.status_code,r.headers,body)
        if state=="ok" and detectar(body):state="captcha_required"
        if state in ("blocked_waf","rate_limited","captcha_required"):self.cooldown.set(host,state,3600 if state!="rate_limited" else min(3600,int(r.headers.get("Retry-After",60)) if str(r.headers.get("Retry-After","")).isdigit() else 60))
        return {"state":state,"status":r.status_code,"body":body,"url":r.url,"headers":dict(r.headers)}
