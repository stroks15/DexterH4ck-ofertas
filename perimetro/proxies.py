# perimetro/proxies.py
from __future__ import annotations

import json
import os
import random
import threading
import time
from urllib.parse import unquote, urlsplit

CASTIGO_SEG = 180.0


def _desde_url(bruto: str) -> dict[str, str] | None:
    texto = bruto.strip()
    if not texto:
        return None
    if "://" not in texto:
        texto = "http://" + texto
    partes = urlsplit(texto)
    if not partes.hostname:
        return None
    servidor = f"{partes.scheme}://{partes.hostname}"
    if partes.port:
        servidor += f":{partes.port}"
    proxy = {"server": servidor}
    if partes.username:
        proxy["username"] = unquote(partes.username)
        proxy["password"] = unquote(partes.password or "")
    return proxy


def parsear(bruto: str) -> list[dict[str, str]]:
    crudo = (bruto or "").strip()
    if not crudo:
        return []
    if crudo.startswith("["):
        try:
            data = json.loads(crudo)
        except ValueError:
            data = []
        salida = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and item.get("server"):
                limpio = {"server": str(item["server"])}
                if item.get("username"):
                    limpio["username"] = str(item["username"])
                    limpio["password"] = str(item.get("password") or "")
                salida.append(limpio)
            elif isinstance(item, str):
                proxy = _desde_url(item)
                if proxy:
                    salida.append(proxy)
        return salida
    salida = []
    for trozo in crudo.replace("\n", ",").split(","):
        proxy = _desde_url(trozo)
        if proxy:
            salida.append(proxy)
    return salida


class PoolProxies:
    def __init__(self, bruto: str | None = None) -> None:
        self._lock = threading.Lock()
        self._proxies = parsear(bruto if bruto is not None else os.environ.get("PROXY_POOL", ""))
        self._castigados: dict[str, float] = {}
        self._turno = 0
        self.estrategia = (os.environ.get("PROXY_ESTRATEGIA", "round_robin") or "").strip().lower()
        self._pegajoso: dict[str, dict[str, str]] = {}

    def __len__(self) -> int:
        return len(self._proxies)

    def disponibles(self) -> list[dict[str, str]]:
        ahora = time.monotonic()
        with self._lock:
            return [p for p in self._proxies if self._castigados.get(p["server"], 0) <= ahora]

    def elegir(self, host: str = "") -> dict[str, str] | None:
        libres = self.disponibles() or self._proxies
        if not libres:
            return None
        if self.estrategia == "sticky" and host:
            with self._lock:
                fijo = self._pegajoso.get(host)
                if fijo and fijo in libres:
                    return fijo
                elegido = random.choice(libres)
                self._pegajoso[host] = elegido
                return elegido
        with self._lock:
            self._turno = (self._turno + 1) % len(libres)
            return libres[self._turno]

    def castigar(self, proxy: dict[str, str] | None, segundos: float = CASTIGO_SEG) -> None:
        if not proxy:
            return
        with self._lock:
            self._castigados[proxy["server"]] = time.monotonic() + max(5.0, segundos)

    def describir(self) -> str:
        return f"{len(self._proxies)} proxies ({len(self.disponibles())} libres)"
