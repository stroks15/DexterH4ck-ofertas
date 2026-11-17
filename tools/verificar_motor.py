# tools/verificar_motor.py
from __future__ import annotations

import json
import sys

from core.scrapling_motor import Config, Presupuesto, disponible, extraer_minimo


def main() -> int:
    deps = disponible()
    print("dependencias:", json.dumps(deps))
    if not any(deps.values()):
        print("::warning::Scrapling no disponible; el pipeline usará el respaldo legacy.")

    config = Config()
    print(f"perfiles: {len(config.perfiles)} | activos: {len(config.perfiles_activos())} | concurrencia: {config.concurrencia}")
    for perfil in config.perfiles_activos():
        print(f"  {perfil.id:20} {perfil.dispositivo:8} dpr={perfil.dpr} {perfil.ua[:48]}")

    presupuesto = Presupuesto(config.presupuesto)
    print("timeout inicial:", presupuesto.timeout("ejemplo.mx"))
    presupuesto.observar("ejemplo.mx", 5000)
    print("timeout tras latencia:", presupuesto.timeout("ejemplo.mx"))
    print("retry-after 10 ->", presupuesto.espera_retry_after("10"))

    html = '<div class="product"><a class="name" href="/p/1">Cafetera</a><span class="price">$1,299.00</span></div>'
    filas = extraer_minimo(html, {"card": "div.product", "title": "a.name", "price": "span.price", "link": "a.name"})
    print("extraccion:", json.dumps(filas, ensure_ascii=False))
    if not filas:
        print("::error::La extracción mínima no devolvió filas.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
