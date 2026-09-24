# Código actualizado del monitor de ofertas
# Filtros mejorados: descuentos 60-99%, keywords ampliadas y clasificación de ofertas.

MIN_DESCUENTO = 60
MAX_DESCUENTO = 99

TIENDAS = [
    "mercadolibre",
    "liverpool",
    "amazon",
    "walmart",
    "costco",
    "palacio",
    "coppel",
]

KEYWORDS_ML = [
    "iphone", "ipad", "macbook", "apple watch", "airpods",
    "smartphone", "celular", "samsung", "xiaomi", "motorola",
    "laptop", "notebook", "pc gamer", "monitor", "ssd",
    "television", "smart tv", "oled", "qled",
    "playstation", "xbox", "nintendo switch",
    "bocina bluetooth", "audifonos", "soundbar",
    "refrigerador", "lavadora", "secadora", "cafetera",
    "tenis", "nike", "adidas", "puma", "smartwatch",
    "liquidacion", "remate", "outlet", "ultima pieza",
    "oferta relampago"
]

PALABRAS_OFERTA = [
    "80%", "90%", "liquidacion", "remate",
    "ultima pieza", "precio especial", "oferta flash", "outlet"
]


def clasificar_oferta(descuento):
    if descuento >= 90:
        return "🔥 ERROR PRECIO"
    if descuento >= 80:
        return "🚨 OFERTA EXTREMA"
    if descuento >= 60:
        return "🔥 BUENA OFERTA"
    return None
