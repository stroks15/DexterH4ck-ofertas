# perimetro/__init__.py
from __future__ import annotations

__version__ = "1.0.0"

from .registry import TIENDAS, TIENDAS_OBJETIVO, tienda, tiendas_activas  # noqa: E402
from .adaptadores import PerimetroScraper, adaptadores  # noqa: E402

__all__ = ["TIENDAS", "TIENDAS_OBJETIVO", "tienda", "tiendas_activas", "PerimetroScraper", "adaptadores", "__version__"]
