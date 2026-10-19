"""Descuento que declara el propio texto de un mensaje de Telegram ("-70%", "70% OFF").

Muchos grupos/canales publican el porcentaje pero no el precio anterior. Para esas fuentes la
única regla de publicación es que el descuento esté entre 50 % y 99 %. Este módulo solo
*lee* el porcentaje; el rango lo aplican quienes lo usan.

Reglas de lectura (deterministas, sin IA):
* Solo formatos explícitos: ``-36%``, ``70% OFF``, ``70% de descuento``, ``descuento: 70%``.
* Se ignoran porcentajes de cashback, bonificación, cupón, meses sin intereses y los
  precedidos por "hasta" (un tope no es el descuento del producto).
* Un valor fuera de 1-100 no es un descuento.
"""

from __future__ import annotations

import re
from typing import Any

_P_NEGATIVO = re.compile(r"(?<![\w$.,])-\s?(\d{1,3})\s?%")
_P_SUFIJO = re.compile(
    r"(?<![\w.,$])(\d{1,3})\s?%\s*(?:de\s+)?(?:descuento|desc\.?|dto\.?|off)(?![A-Za-zÁÉÍÓÚáéíóúÑñ])", re.I
)
_P_PREFIJO = re.compile(r"(?:descuento|desc\.?|dto\.?|rebaja)\s*(?:de|del)?\s*:?\s*-?\s*(\d{1,3})\s?%", re.I)
_PATRONES = (_P_NEGATIVO, _P_SUFIJO, _P_PREFIJO)

# Porcentajes que NO son el descuento del producto.
_NO_ES_DESCUENTO = re.compile(r"cash\s?back|bonificaci|cup[oó]n|cupon|meses\s+sin\s+intereses|\bmsi\b", re.I)
_HASTA = re.compile(r"hasta\s*$", re.I)


def _linea_de(texto: str, pos: int) -> str:
    inicio = texto.rfind("\n", 0, pos) + 1
    fin = texto.find("\n", pos)
    return texto[inicio : len(texto) if fin < 0 else fin]


def descuento_declarado(texto: Any) -> int | None:
    """Primer descuento (1-100) declarado en ``texto``, o ``None`` si no hay uno explícito."""
    contenido = str(texto or "").replace("\xa0", " ")
    candidatos: list[tuple[int, int]] = []
    for patron in _PATRONES:
        for m in patron.finditer(contenido):
            valor = int(m.group(1))
            if not 1 <= valor <= 100:
                continue
            if _HASTA.search(contenido[max(0, m.start() - 12) : m.start()]):
                continue
            if _NO_ES_DESCUENTO.search(_linea_de(contenido, m.start())):
                continue
            candidatos.append((m.start(), valor))
    if not candidatos:
        return None
    return min(candidatos)[1]  # el que aparece primero en el texto
