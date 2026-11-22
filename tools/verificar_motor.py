from __future__ import annotations
import sys
from pathlib import Path

# Permite ejecutar exactamente `python tools/verificar_motor.py` desde la raíz.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapers.api_stores import api_first_scrapers
from perimetro.registry import TIENDAS

def main():
    adapters=api_first_scrapers()
    names=[getattr(x,"store","") for x in adapters]
    assert TIENDAS, "registry vacío"
    assert names and names[0] != "", "sin adaptadores"
    # Motor al frente; Mercado Libre legacy se conserva detrás.
    assert any(n == "Mercado Libre MX" for n in names), "Mercado Libre legacy ausente"
    print("motor OK: %d tiendas registradas; %d adaptadores" % (len(TIENDAS),len(adapters)))
if __name__=="__main__":main()
