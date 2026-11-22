"""Perfiles descriptivos de navegador; no suplantan sesiones ni evaden WAF."""
from __future__ import annotations
import json
from pathlib import Path
CFG=Path(__file__).resolve().parents[1]/"config"/"scrapling_motor.json"
try: CONFIG=json.loads(CFG.read_text(encoding="utf-8"))
except (OSError,ValueError): CONFIG={}
def perfil(host: str) -> dict:
    key=CONFIG.get("perfil_por_host",{}).get(host,"desktop_chrome_windows")
    return dict(CONFIG.get("perfiles",{}).get(key,{}))
def headers(host: str) -> dict:
    p=perfil(host); h={"User-Agent":p.get("user_agent","DexterH4ck-ofertas/2.0 (+deal-monitor; es-MX)"),"Accept-Language":"es-MX,es;q=0.9,en;q=0.7","Accept":"text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
    if p.get("sec_ch_ua"):h.update({"sec-ch-ua":p["sec_ch_ua"],"sec-ch-ua-platform":'"'+p.get("plataforma","")+'"',"sec-ch-ua-mobile":"?1" if p.get("movil") else "?0"})
    return h
def fetcher_kwargs(host: str) -> dict:
    return {"headers":headers(host),"timeout":12,"retries":0}
def stealthy_kwargs(host: str) -> dict:
    # Intencionalmente sin humanize/solver/patches de fingerprint: solo navegador estándar.
    return {"headers":headers(host),"timeout":12,"retries":0}
