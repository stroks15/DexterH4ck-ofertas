from __future__ import annotations
import os, threading, time

class Proxies:
    """Pool opcional; ausente significa conexión directa, sin inventar proveedores."""
    def __init__(self, values=None, mode=None):
        raw=values if values is not None else os.getenv("PROXY_POOL","")
        self.values=[x.strip() for x in (raw.splitlines() if isinstance(raw,str) else raw) if str(x).strip()]
        self.mode=mode or os.getenv("PROXY_MODE","round_robin"); self._idx=0; self._sticky={}; self._penalty={}; self._lock=threading.Lock()
    def get(self, host: str):
        with self._lock:
            self._penalty={k:v for k,v in self._penalty.items() if v>time.monotonic()}
            available=[p for p in self.values if self._penalty.get(p,0)<=time.monotonic()]
            if not available:return None
            if self.mode=="sticky":
                p=self._sticky.get(host)
                if p in available:return p
                p=available[self._idx%len(available)]; self._idx+=1; self._sticky[host]=p; return p
            p=available[self._idx%len(available)]; self._idx+=1; return p
    def punish(self, proxy: str, seconds: int=300):
        with self._lock:self._penalty[proxy]=time.monotonic()+max(1,seconds)
