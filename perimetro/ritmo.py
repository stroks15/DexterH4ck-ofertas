# perimetro/ritmo.py
from __future__ import annotations

import os
import random
import threading
import time


def _env_float(nombre: str, defecto: float, minimo: float, maximo: float) -> float:
    try:
        valor = float(os.environ.get(nombre, "") or defecto)
    except (TypeError, ValueError):
        return defecto
    return max(minimo, min(maximo, valor))


class Ritmo:
    def __init__(self, rps: float | None = None, rafaga: float = 3.0) -> None:
        self.rps = rps if rps is not None else _env_float("PERIMETRO_RPS", 0.5, 0.05, 10.0)
        self.rafaga = max(1.0, rafaga)
        self._cubos: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def _espera(self, host: str) -> float:
        ahora = time.monotonic()
        with self._lock:
            fichas, marca = self._cubos.get(host, (self.rafaga, ahora))
            fichas = min(self.rafaga, fichas + (ahora - marca) * self.rps)
            if fichas >= 1.0:
                self._cubos[host] = (fichas - 1.0, ahora)
                return 0.0
            falta = (1.0 - fichas) / self.rps
            self._cubos[host] = (0.0, ahora + falta)
            return falta

    def esperar(self, host: str) -> float:
        espera = self._espera(host)
        if espera > 0:
            time.sleep(espera + random.uniform(0.05, 0.35))
        return espera

    def penalizar(self, host: str, segundos: float) -> None:
        with self._lock:
            self._cubos[host] = (0.0, time.monotonic() + max(0.0, segundos))

    @staticmethod
    def backoff(intento: int, base: float = 1.2, tope: float = 20.0) -> float:
        return min(tope, base * (2 ** max(0, intento))) + random.uniform(0.2, 0.9)
