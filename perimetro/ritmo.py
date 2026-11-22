from __future__ import annotations
import random, threading, time
from collections import defaultdict
from urllib.parse import urlparse

class Ritmo:
    def __init__(self, rate: float = 0.5, capacity: int = 1):
        self.rate=max(0.05,float(rate)); self.capacity=max(1,int(capacity)); self._state=defaultdict(lambda:[self.capacity,time.monotonic()]); self._lock=threading.Lock()
    def adquirir(self, url: str) -> None:
        host=urlparse(url).netloc.lower()
        while True:
            with self._lock:
                tokens, updated=self._state[host]; now=time.monotonic(); tokens=min(self.capacity,tokens+(now-updated)*self.rate)
                if tokens >= 1: self._state[host]=[tokens-1,now]; return
                wait=(1-tokens)/self.rate; self._state[host]=[tokens,now]
            time.sleep(min(wait,10))

def backoff(attempt: int, retry_after: float = 0) -> None:
    time.sleep(min(30.0,max(0.0,retry_after),30.0) if retry_after else min(8.0,0.5*(2**attempt))*random.uniform(0.75,1.25))
