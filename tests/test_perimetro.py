# tests/test_perimetro.py
# unittest puro: el workflow corre "unittest discover", no pytest.
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from perimetro import extraccion, registry, waf
from perimetro.registry import Tienda

RAIZ = Path(__file__).resolve().parent.parent


class PruebasPerimetro(unittest.TestCase):
    def setUp(self):
        self.tiendas = registry.recargar()

    def test_perimetro_completo(self):
        esperadas = {"walmart", "bodega", "chedraui", "soriana", "sams", "coppel", "elektra", "liverpool", "suburbia", "sears", "nike", "adidas", "palacio", "homedepot", "officedepot", "lacomer", "amazon"}
        self.assertTrue(esperadas.issubset({t.id for t in self.tiendas}))

    def test_estrategias_validas(self):
        for t in self.tiendas:
            self.assertTrue(t.estrategias, t.id)
            self.assertTrue(set(t.estrategias) <= registry.ESTRATEGIAS_VALIDAS, t.id)
            self.assertTrue(t.busqueda or t.vtex_host or t.sfcc_site, t.id)

    def test_url_busqueda_codifica(self):
        t = Tienda(id="x", nombre="X", home="https://x.mx", estrategias=("html",), busqueda="https://x.mx/s?q={q}")
        self.assertEqual(t.url_busqueda("liquidacion 50%"), "https://x.mx/s?q=liquidacion+50%25")

    def test_clasificar_challenge_es_bloqueo(self):
        self.assertEqual(waf.clasificar(200, {"cf-mitigated": "challenge"}, "Just a moment..."), "blocked_waf")
        self.assertEqual(waf.clasificar(200, {}, "please solve the g-recaptcha"), "captcha_required")
        self.assertEqual(waf.clasificar(200, {}, "<html>productos normales</html>"), "ok")

    def test_cooldown_ida_y_vuelta(self):
        with tempfile.TemporaryDirectory() as tmp:
            cooldown = waf.Cooldown(Path(tmp) / "c.json", minutos=5)
            cooldown.registrar("x.mx", "blocked_waf", "cloudflare")
            self.assertIsNotNone(cooldown.activo("x.mx"))
            cooldown.limpiar("x.mx")
            self.assertIsNone(cooldown.activo("x.mx"))

    def test_vtex_extrae_mejor_oferta(self):
        payload = [{"productId": "1", "productName": "Televisor", "link": "/p/televisor/1", "items": [{"sellers": [{"commertialOffer": {"Price": 1000, "ListPrice": 2000, "AvailableQuantity": 3}}, {"commertialOffer": {"Price": 800, "ListPrice": 2000, "AvailableQuantity": 1}}]}]}]
        filas = extraccion.vtex(payload, "Chedraui", "https://www.chedraui.com.mx")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["precio_actual"], 800)
        self.assertEqual(filas[0]["descuento"], 60)

    def test_jsonld_itemlist(self):
        cuerpo = '<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"ListItem","item":{"@type":"Product","name":"Silla","sku":"S1","offers":{"price":"450"},"url":"/p/silla/1"}}]}</script>'
        filas = extraccion.jsonld(cuerpo, "Sears", "https://www.sears.com.mx")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["precio_actual"], 450)

    def test_selectores_respaldo(self):
        cuerpo = '<div class="product"><a class="name" href="/p/x/9">Cafetera</a><span class="price">$1,299.00</span><s>$2,000.00</s><span class="pct">35% de descuento</span></div>'
        filas = extraccion.selectores(cuerpo, "Soriana", "https://www.soriana.com", {"card": "div.product", "title": "a.name", "price": "span.price", "link": "a.name"})
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["descuento"], 35)
        self.assertEqual(filas[0]["url"], "https://www.soriana.com/p/x/9")

    def test_deduplicar(self):
        filas = [{"id": "1", "url": "/a"}, {"id": "1", "url": "/a"}, {"id": "2", "url": "/b"}]
        self.assertEqual(len(extraccion.deduplicar(filas)), 2)

    def test_config_json_valido(self):
        data = json.loads((RAIZ / "config" / "perimetro_tiendas.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["tiendas"]), 17)


if __name__ == "__main__":
    unittest.main()
