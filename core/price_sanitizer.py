"""Sanitización estricta de precios.

Problema que resuelve: cadenas de tamaño/gramaje ("875g", "1 kg", "400 ml") se
interpretaban como precio (p. ej. como ``precio_anterior``/``wasPrice``) y generaban
descuentos falsos del 90-99 %.

Reglas:
1. Un precio SOLO se acepta si es un número real (int/float/Decimal) o una cadena que
   sea *únicamente* un monto (``"849.01"``, ``"$1,299.00"``, ``"1299 MXN"``).
   Cualquier cadena con unidades o texto extra (``"875g"``, ``"1 kg"``) se rechaza.
2. Nunca se extrae el primer número de un texto libre.
3. Defensa en profundidad: si el precio de referencia coincide con una medida
   declarada en el título/descripción (875 de "875g", 1000 de "1 kg"), se descarta.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_PRECIO = 2_000_000.0

# Un monto "limpio" y nada más. Con miles (1,299.00), sin miles (1299.5) y moneda opcional.
_PRECIO_LIMPIO = re.compile(
    r"^\s*\$?\s*(?P<monto>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(?:MXN|MN|M\.N\.)?\s*$",
    re.IGNORECASE,
)

# Claves de dict que SÍ contienen un número de precio estructurado.
_CLAVES_MONTO = ("price", "amount", "value")

_UNIDADES = (
    r"kg|kgs|kilos?|kilogramos?|gr|grs|gramos?|g|mg|ml|mls|cc|lt|lts|litros?|l|"
    r"oz|onzas?|lb|lbs|libras?|pzas?|pzs?|pz|piezas?|pack|paquetes?|"
    r"cm|mm|mts?|metros?|m|gb|tb|mb|mah|w|watts?|v|hz|pulgadas?|pulg|in|rollos?|hojas?|capsulas?|cápsulas?|tabletas?"
)
# Número seguido de unidad. No debe venir precedido por "$" (eso ya es un precio).
_MEDIDA = re.compile(
    rf"(?<![\w.$,])(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>{_UNIDADES})(?![A-Za-zÁÉÍÓÚáéíóúÑñ])",
    re.IGNORECASE,
)

_CAMPOS_TEXTO = (
    "titulo",
    "title",
    "nombre",
    "name",
    "description",
    "descripcion",
    "presentacion",
    "contenido",
    "gramaje",
    "size",
    "tamano",
    "tamaño",
)


def parse_price(value: Any) -> float | None:
    """Devuelve un precio positivo o ``None``. Nunca adivina a partir de texto libre."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        for clave in _CLAVES_MONTO:
            if clave in value:
                return parse_price(value[clave])
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            numero = float(value)
        except (TypeError, ValueError, InvalidOperation):
            return None
    elif isinstance(value, str):
        coincide = _PRECIO_LIMPIO.match(value)
        if not coincide:
            return None
        numero = float(coincide.group("monto").replace(",", ""))
    else:
        return None
    if not math.isfinite(numero) or numero <= 0 or numero > MAX_PRECIO:
        return None
    return numero


def extraer_medidas(texto: Any) -> set[float]:
    """Cantidades con unidad presentes en el texto (incluye equivalentes en g/ml)."""
    medidas: set[float] = set()
    for m in _MEDIDA.finditer(str(texto or "")):
        try:
            n = float(m.group("n").replace(",", "."))
        except ValueError:
            continue
        medidas.add(n)
        unidad = m.group("u").lower()
        if unidad in ("kg", "kgs", "kilo", "kilos", "kilogramo", "kilogramos", "l", "lt", "lts", "litro", "litros"):
            medidas.add(n * 1000)  # 1 kg -> 1000 g ; 1 l -> 1000 ml
    return medidas


def texto_del_item(item: dict) -> str:
    return " ".join(str(item.get(k) or "") for k in _CAMPOS_TEXTO)


def coincide_con_medida(valor: Any, texto: Any) -> bool:
    """True si ``valor`` es igual a una medida declarada (875 vs "875g")."""
    numero = parse_price(valor)
    if numero is None:
        return False
    return any(abs(numero - medida) < 0.005 for medida in extraer_medidas(texto))


def sanear_par_precios(actual: Any, anterior: Any, texto: Any = "") -> tuple[float | None, float | None, str]:
    """(actual, anterior, motivo). ``anterior`` queda en ``None`` si es sospechoso.

    motivo: "" si todo está bien, o la razón por la que se descartó la referencia.
    """
    precio = parse_price(actual)
    previo_crudo = anterior
    previo = parse_price(anterior)
    if previo_crudo not in (None, "", 0, 0.0) and previo is None:
        return precio, None, "anterior_no_numerico"  # p. ej. "875g"
    if precio is None:
        return None, None, "actual_invalido"
    if previo is not None and previo <= precio:
        return precio, None, "anterior_no_mayor"
    if previo is not None and coincide_con_medida(previo, texto):
        # Solo es dañino si fabricaría un descuento alto; con ese ratio no es una rebaja real.
        if (1 - precio / previo) * 100 >= 50:
            return precio, None, "anterior_igual_a_gramaje"
    return precio, previo, ""
