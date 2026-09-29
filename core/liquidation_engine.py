"""Motor de detección de liquidaciones para DexterH4ck-ofertas."""

import re

KEYWORDS = {
    "liquidacion": 25,
    "remate": 20,
    "saldo": 20,
    "outlet": 15,
    "ultima pieza": 15,
    "open box": 10,
    "warehouse": 10,
    "clearance": 10,
    "oferta relampago": 10,
}


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def calculate_discount(old_price, new_price):
    try:
        old_price = float(old_price)
        new_price = float(new_price)
        if old_price <= 0 or new_price <= 0 or old_price <= new_price:
            return 0
        return round(((old_price - new_price) / old_price) * 100)
    except (TypeError, ValueError):
        return 0


def evaluate_product(product):
    text = normalize_text(
        f"{product.get('title', '')} {product.get('description', '')}"
    )

    indicators = []
    score = 0

    for word, points in KEYWORDS.items():
        if word in text:
            indicators.append(word)
            score += points

    discount = calculate_discount(
        product.get("previous_price", product.get("precio_anterior", 0)),
        product.get("price", product.get("precio_actual", 0)),
    )

    score += min(discount // 2, 50)

    if product.get("store") or product.get("tienda"):
        score += 10

    return {
        "es_liquidacion": bool(indicators) or discount >= 60,
        "descuento": discount,
        "indicadores": indicators,
        "nivel_oportunidad": min(score, 100),
        "recomendado": score >= 60 or discount >= 60,
    }


def score_product(product):
    return evaluate_product(product)["nivel_oportunidad"]


def is_liquidation(product):
    return evaluate_product(product)["recomendado"]
