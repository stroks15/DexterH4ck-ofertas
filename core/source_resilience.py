"""Utilidades de resiliencia para fuentes comerciales.

No intenta saltar WAF/CAPTCHA. Clasifica respuestas bloqueadas, aplica backoff
conservador y permite que el monitor cambie a una fuente alternativa.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Any

TRANSIENT = {408, 425, 429, 500, 502, 503, 504, 522, 524}
STOP_THIS_CYCLE = {401, 403, 404, 407, 451}


class SourceCircuit:
    def __init__(self, name: str):
        self.name = name
        self.failures = defaultdict(int)
        self.paused = False
        self.reason = ""

    def record(self, status: int | None, reason: str = "") -> bool:
        if status is None:
            return False
        self.failures[status] += 1
        if status in STOP_THIS_CYCLE:
            self.paused = True
            self.reason = reason or f"HTTP {status}"
        elif status in TRANSIENT and self.failures[status] >= 2:
            self.paused = True
            self.reason = reason or f"HTTP {status} repetido"
        return self.paused

    def can_continue(self) -> bool:
        return not self.paused

    def backoff(self, status: int | None, attempt: int = 0, retry_after: Any = None) -> None:
        if status not in TRANSIENT:
            return
        try:
            server = float(retry_after)
        except (TypeError, ValueError):
            server = 0.0
        delay = server if server > 0 else min(12.0, 2.0 ** attempt)
        delay += random.uniform(0.15, 0.65)
        time.sleep(delay)


def status_summary(circuit: SourceCircuit) -> dict[str, Any]:
    return {
        "paused": circuit.paused,
        "reason": circuit.reason,
        "failures": dict(circuit.failures),
    }
