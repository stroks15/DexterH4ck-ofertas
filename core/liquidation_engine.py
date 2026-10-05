"""Motor de puntuación y detección de liquidaciones para DexterH4ck-ofertas."""

import json
import re
import unicodedata
from pathlib import Path

from core.extreme_liquidation import analizar_precio_extremo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_CATEGORIAS = ROOT / "config" / "categorias_ofertas.json"
CONFIG_MARCAS = ROOT / "marcas_prioritarias.json"

KEYWORDS_LIQUIDACION = {
    "liquidacion": 25,
    "liquidación": 25,
    "remate": 20,
    "saldo": 20,
    "saldos": 20,
    "outlet": 15,
    "ultima pieza": 15,
    "última pieza": 15,
    "ultimas piezas": 15,
    "últimas piezas": 15,
    "open box": 10,
    "warehouse": 10,
    "clearance": 10,
    "oferta relampago": 10,
    "oferta relámpago": 10,
    "precio extremo": 20,
    "precio error": 15,
    "error de precio": 15,
}

def normalize_text(value):
    text = re.sub(r"\s+", " ", str(value or "").lower()).strip()
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")

def _load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return default

def _config():
    categorias = _load_json(CONFIG_CATEGORIAS, {"categorias": [], "alta_demanda": [], "aliases": {}})
    marcas = _load_json(CONFIG_MARCAS, {})
    return categorias, marcas

def infer_category(product):
    categorias, _ = _config()
    explicit = product.get("categoria") or product.get("category")
    if explicit:
        aliases = categorias.get("aliases", {})
        key = normalize_text(explicit)
        if key in aliases:
            return aliases[key]
        for category in categorias.get("categorias", []):
            if normalize_text(category) == key:
                return category

    text = normalize_text(" ".join(str(product.get(k, "")) for k in (
        "titulo", "title", "nombre", "description", "descripcion", "subcategoria"
    )))
    aliases = categorias.get("aliases", {})
    for alias in sorted(aliases, key=lambda x: len(normalize_text(x)), reverse=True):
        if re.search(rf"(?<!\w){re.escape(normalize_text(alias))}(?!\w)", text):
            return aliases[alias]
    return ""

def detect_priority_brand(product):
    _, brands = _config()
    explicit = str(product.get("marca") or product.get("brand") or "").strip()
    text = normalize_text(" ".join(str(product.get(k, "")) for k in (
        "titulo", "title", "nombre", "description", "descripcion", "marca", "brand"
    )))
    for category, values in brands.items():
        for brand in values:
            normalized = normalize_text(brand)
            if normalized and (
                normalized == normalize_text(explicit)
                or re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", text)
            ):
                return brand, category
    return explicit, ""

def calculate_discount(old_price, new_price):
    try:
        old_price = float(old_price)
        new_price = float(new_price)
        if old_price <= 0 or new_price <= 0 or old_price <= new_price:
            return 0
        return round(((old_price - new_price) / old_price) * 100)
    except (TypeError, ValueError):
        return 0

def score_product(product, discount=None):
    categorias, _ = _config()
    if discount is None:
        discount = calculate_discount(
            product.get("previous_price", product.get("precio_anterior", 0)),
            product.get("price", product.get("precio_actual", 0)),
        )

    text = normalize_text(" ".join(str(product.get(k, "")) for k in (
        "titulo", "title", "nombre", "description", "descripcion", "liquidacion", "indicadores"
    )))
    indicators = []
    # El descuento es el componente principal: aporta de 5 a 100 puntos.
    # 50% = ~53 puntos; 90% = ~91; 99% = 100. Los demás indicadores
    # funcionan como bonos de prioridad, con tope final de 100.
    descuento_normalizado = min(max(float(discount or 0), 0), 99)
    discount_score = round(5 + (descuento_normalizado / 99) * 95)
    score = discount_score

    brand, brand_group = detect_priority_brand(product)
    if brand_group:
        score += 20
        indicators.append(f"marca:{brand}")

    category = infer_category(product)
    high_demand = any(
        normalize_text(category) == normalize_text(x)
        for x in categorias.get("alta_demanda", [])
    )
    if high_demand:
        score += 15
        indicators.append(f"categoria_alta:{category}")

    keyword_bonus = 0
    for keyword, points in KEYWORDS_LIQUIDACION.items():
        if normalize_text(keyword) in text:
            keyword_bonus = max(keyword_bonus, points)
    if keyword_bonus:
        score += min(keyword_bonus, 25)
        indicators.append("liquidacion")

    last_piece_outlet = any(
        term in text for term in ("ultima pieza", "ultimas piezas", "outlet", "clearance")
    )
    if product.get("outlet") is True or last_piece_outlet:
        score += 10
        indicators.append("ultima_pieza_outlet")

    extreme = analizar_precio_extremo(product)
    # Liquidaciones físicas por centavos: señal independiente del texto.
    # Se aplica a Walmart/Bodega (.01/.02/.03) y Soriana (.02/.05).
    if "liquidacion_terminacion_centavos" in extreme.get("senales", []):
        score += 30
        indicators.append("liquidacion_centavos_fisica")
    if extreme["es_extremo"]:
        indicators.append("precio_extremo")
        score += 15
        if extreme["url_oficial"]:
            score += 5
            indicators.append("url_tienda_oficial")
        if extreme["precio_verificado"]:
            score += 20
            indicators.append("precio_extremo_verificado")

    return {
        "puntuacion": max(5, min(score, 100)),
        "componente_descuento": discount_score,
        "descuento": int(discount or 0),
        "marca": brand,
        "marca_prioritaria": bool(brand_group),
        "categoria": category,
        "categoria_alta_demanda": high_demand,
        "indicadores": indicators,
        "extremo": extreme,
    }

def evaluate_product(product):
    result = score_product(product)
    result["es_liquidacion"] = 5 <= result["descuento"] <= 99
    result["nivel_oportunidad"] = result["puntuacion"]
    # La puntuación NO decide si una liquidación 40..99 se publica; solamente
    # sirve para ordenar/priorizar. El porcentaje es el criterio de elegibilidad.
    result["recomendado"] = result["es_liquidacion"] or "liquidacion_centavos_fisica" in result["indicadores"]

    texto = " ".join(str(product.get(k, "")) for k in (
        "titulo", "description", "descripcion", "publicacion", "condiciones"
    )).lower()
    condiciones = list(product.get("condiciones") or [])
    for needles, label in (
        (("cupón", "cupon", "coupon", "código promocional"), "cupón"),
        (("planea y ahorra", "subscribe & save"), "Planea y Ahorra"),
        (("seguir la tienda", "seguidor de la tienda"), "seguir la tienda"),
        (("compra mínima", "mínimo de compra", "minima de compra"), "compra mínima"),
        (("por volumen", "compra 5", "compra 10"), "cantidad/volumen"),
        (("prime",), "Amazon Prime"),
    ):
        if any(x in texto for x in needles) and label not in condiciones:
            condiciones.append(label)
    return result

def is_liquidation(product):
    return evaluate_product(product)["recomendado"]
