import unittest
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from scrapers import tiendas_mexico

from core.price_sanitizer import sanitize_history
from core.liquidation_engine import score_product
from tools.telegram_bot import clean_product_url, format_alert, rango_descuento


class TierTests(unittest.TestCase):
    def test_tier_emoji_ranges(self):
        self.assertEqual(rango_descuento(50)[0], "🏷️")
        self.assertEqual(rango_descuento(69)[0], "🏷️")
        self.assertEqual(rango_descuento(70)[0], "🔥")
        self.assertEqual(rango_descuento(89)[0], "🔥")
        self.assertEqual(rango_descuento(90)[0], "⚡")
        self.assertEqual(rango_descuento(94)[0], "⚡")
        self.assertEqual(rango_descuento(95)[0], "💣")
        self.assertEqual(rango_descuento(99)[0], "💣")

    def test_clean_url_rejects_unresolved_markers(self):
        self.assertIsNone(clean_product_url("https://shop.mx/p/null"))
        self.assertIsNone(clean_product_url("https://shop.mx/p/1?ref=undefined"))
        self.assertIsNone(clean_product_url("javascript:alert(1)"))
        self.assertIsNotNone(clean_product_url("https://shop.mx/p/123"))

    def test_message_is_markdown_v2_and_diamond_leads(self):
        alert = {"item": {"tienda": "Walmart MX", "titulo": "TV Samsung 55 pulgadas", "brand": "Samsung",
                           "category": "Tecnología", "url": "https://www.walmart.com.mx/ip/tv/123"},
                 "actual": 99, "referencia": 2000, "descuento": 95, "evaluacion": {"puntuacion": 92}}
        text = format_alert(alert)
        self.assertTrue(text.startswith("💣 *¡LIQUIDACIÓN"))
        self.assertIn("*Descuento:* 95% REALES", text)
        self.assertIn("$2,000\\.00", text)
        self.assertIn("[Ver Oferta en la Tienda]", text)
        self.assertIn("\\|", text)
        self.assertIn("$99\\.00", text)


class ScoreFormulaTests(unittest.TestCase):
    def test_score_components_follow_requested_formula(self):
        product = {"titulo": "Liquidación última pieza Hasbro outlet", "marca": "Hasbro", "categoria": "Bebés", "tipo_fuente": "ONLINE"}
        result = score_product(product, 99)
        self.assertEqual(result["componente_descuento"], 50)
        self.assertTrue(result["marca_prioritaria"])
        self.assertTrue(result["categoria_alta_demanda"])
        self.assertIn("liquidacion", result["indicadores"])
        self.assertIn("ultima_pieza_outlet", result["indicadores"])
        self.assertEqual(result["puntuacion"], 100)  # 50 + 20 + 15 + 10 + 10, capped

    def test_centavos_solo_bono_para_fisica(self):
        from core.liquidation_engine import score_product
        online = score_product({"tienda": "Walmart MX", "precio_actual": 100.03, "tipo_fuente": "ONLINE"}, 0)
        physical = score_product({"tienda": "Walmart MX", "precio_actual": 100.03, "tipo_fuente": "FISICA"}, 0)
        self.assertNotIn("liquidacion_centavos_fisica", online["indicadores"])
        self.assertIn("liquidacion_centavos_fisica", physical["indicadores"])
        self.assertEqual(physical["puntuacion"] - online["puntuacion"], 30)


class HistoryRepairTests(unittest.TestCase):
    def test_history_grammage_repaired_in_memory(self):
        history = {"k": {"titulo": "Leche 1 kg", "precio_actual": 89, "precio_maximo": 1000,
                         "precio_anterior": 1000}}
        fixed, count = sanitize_history(history)
        self.assertEqual(count, 2)
        self.assertEqual(fixed["k"]["precio_maximo"], 89)
        self.assertIsNone(fixed["k"]["precio_anterior"])


class ScraplingBlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scrapers import tiendas_mexico
        if not tiendas_mexico.SCRAPLING_AVAILABLE:
            raise unittest.SkipTest("Scrapling no está instalado; instala requirements-browser.txt")

    def test_waf_response_is_recorded_without_retry(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/robots.txt":
                    self.send_response(200); self.end_headers(); self.wfile.write(b"User-agent: *\\nAllow: /"); return
                self.send_response(403); self.send_header("Retry-After", "90"); self.end_headers(); self.wfile.write(b"Access denied")
            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.object(tiendas_mexico, "COOLDOWN_FILE", Path(tmp) / "cooldown.json"):
                result = tiendas_mexico.MexicoLegacySpider({"amazon": f"http://127.0.0.1:{server.server_port}/search?q={{q}}"}).start()
                self.assertEqual(len(result.items), 0)
                self.assertEqual(tiendas_mexico.LAST_STATUS["amazon"]["reason"], "HTTP_403_WAF_BLOCK")
                saved = json.loads((Path(tmp) / "cooldown.json").read_text(encoding="utf-8"))
                self.assertGreaterEqual(saved["amazon"]["blocked_until"], saved["amazon"]["updated_at"])
        finally:
            server.shutdown()
            thread.join(timeout=2)


class ApiFirstFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scrapers import tiendas_mexico
        if not tiendas_mexico.SCRAPLING_AVAILABLE:
            raise unittest.SkipTest("Scrapling no está instalado; instala requirements-browser.txt")

    def test_scrapling_only_runs_for_stores_without_api_products(self):
        import monitor_ofertas as mon
        from monitor_ofertas import Fuente
        from unittest.mock import patch

        amazon = {"tienda": "Amazon MX", "titulo": "Producto", "url": "https://www.amazon.com.mx/dp/B012345678", "precio_actual": 100}
        api_amazon = Fuente("api:Amazon MX", {"amazon"}, lambda: [amazon])
        api_soriana = Fuente("api:Soriana", {"soriana"}, lambda: [])
        legacy = Fuente("legacy:tiendas_mexico", {"amazon", "soriana", "liverpool"}, lambda: [])
        llamadas = []

        def fake_legacy(filtro_tienda=None):
            llamadas.append(set(filtro_tienda or []))
            return [{"tienda": "Soriana", "titulo": "Producto S", "url": "https://www.soriana.com/p/12345678", "precio_actual": 50}]

        with (
            patch.object(mon, "definir_fuentes", return_value=[api_amazon, api_soriana, legacy]),
            patch.object(mon, "_tiendas_bloqueadas", return_value=set()),
            patch("scrapers.tiendas_mexico._configured_urls", return_value={"amazon": "https://a.test/?q={q}", "soriana": "https://s.test/?q={q}", "liverpool": "https://l.test/?q={q}"}),
            patch("scrapers.tiendas_mexico.buscar_todas", side_effect=fake_legacy),
            patch.object(mon, "FUENTES_TIMEOUT_TOTAL", 5),
        ):
            result = mon.ejecutar_orquestacion_paralela()

        self.assertEqual(llamadas, [{"soriana", "liverpool"}])
        self.assertEqual(len(result), 2)
        self.assertEqual({x["tienda"] for x in result}, {"Amazon MX", "Soriana"})


if __name__ == "__main__":
    unittest.main()
