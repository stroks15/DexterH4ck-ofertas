from __future__ import annotations
import json, os, re, threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SIGS={"cloudflare":(r"just a moment",r"cf-chl-",r"cloudflare"),"akamai":(r"akamai",r"reference\s*#"),"datadome":(r"datadome",r"dd\s*b="),"perimeterx":(r"perimeterx",r"_pxcaptcha"),"incapsula":(r"incapsula",r"visid_incap"),"kasada":(r"kasada",r"x-kpsdk")}
def clasificar(status: int|None, headers=None, body: str="") -> str:
    headers={str(k).lower():str(v).lower() for k,v in (headers or {}).items()}; text=(body or "")[:200000].lower()
    if status==429:return "rate_limited"
    if status in (401,403,412,451):return "blocked_waf"
    if status is None:return "timeout"
    if status<200 or status>=400:return "invalid_response"
    for vendor,patterns in SIGS.items():
        if any(re.search(p,text) for p in patterns) or (vendor in " ".join(headers.values())):return "blocked_waf"
    if "just a moment" in text or "cf-chl-" in text:return "blocked_waf"
    return "ok"

class Cooldown:
    def __init__(self,path=None):self.path=Path(path or os.getenv("PERIMETRO_COOLDOWN_FILE","perimetro_cooldown.json")); self.lock=threading.Lock()
    def active(self,host):
        try:
            row=json.loads(self.path.read_text()).get(host,{}); return datetime.fromisoformat(row["until"])>datetime.now(timezone.utc)
        except (OSError,ValueError,KeyError,TypeError):return False
    def set(self,host,state,seconds=1800):
        with self.lock:
            try:data=json.loads(self.path.read_text())
            except (OSError,ValueError):data={}
            data[host]={"state":state,"until":(datetime.now(timezone.utc)+timedelta(seconds=max(60,seconds))).isoformat()}
            self.path.parent.mkdir(parents=True,exist_ok=True); tmp=self.path.with_suffix(self.path.suffix+".tmp"); tmp.write_text(json.dumps(data)); tmp.replace(self.path)
