"""Motor de detección de liquidaciones para DexterH4ck-ofertas.

Evalúa productos encontrados por scrapers y genera una puntuación de oportunidad.
"""

KEYWORDS = {
    "liquidacion", "remate", "saldo", "outlet", "ultima pieza",
    "open box", "warehouse", "clearance", "oferta relampago"
}


def normalize_text(value: str) -> str:
    return (value or "").lower().strip()


def calculate_discount(old_price, new_price):
    if not old_price or old_price <= 0:
        return 0
    return round(((old_price - new_price) / old_price) * 100, 2)


def score_product(product):
    title = normalize_text(product.get("title"))
    score = 0

    if any(word in title for word in KEYWORDS):
        score += 40

    discount = calculate_discount(
        product.get("previous_price", 0),
        product.get("price", 0)
    )

    if discount >= 50:
        score += 40
    elif discount >= 30:
        score += 25

    if product.get("store"):
        score += 10

    return min(score, 100)


def is_liquidation(product):
    return score_product(product) >= 50
