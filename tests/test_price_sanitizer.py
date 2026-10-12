import unittest
from unittest.mock import MagicMock, patch

from core.extreme_liquidation import analizar_precio_extremo, senal_terminacion_liquidacion
from core.liquidation_engine import evaluate_product
from core.network_json import extract_products, number
from core.price_sanitizer import coincide_con_medida, parse_price, sanear_par_precios
from scrapers.google_proxy_scraper import GoogleProxyScraper
from scrapers.walmart_graphql import WalmartGraphQLParser
import monitor_ofertas as mo


class ParsePriceTests(unittest.TestCase):
    def test_rechaza_gramajes_y_acepta_montos(self):
        for malo in ("875g", "1kg", "400 ml", "875 g", "1.5 L", "Pack 6", True, None, "", 0, -5, float("nan")):
            self.assertIsNone(parse_price(malo), malo)
        self.assertEqual(parse_price("849.01"), 849.01)
        self.assertEqual(parse_price("$1,299.00"), 1299.0)
        self.assertEqual(parse_price(55), 55.0)
        self.assertEqual(parse_price({"price": "$55.50"}), 55.5)

    def test_network_json_number_ya_no_extrae_el_primer_numero(self):
        self.assertIsNone(number("875g"))
        self.assertIsNone(number("1 kg"))
        self.assertEqual(number("1,299.00"), 1299.0)

    def test_coincide_con_medida(self):
        self.assertTrue(coincide_con_medida(875, "Aceite 875g"))
        self.assertTrue(coincide_con_medida(1000, "Arroz 1 kg"))
        self.assertTrue(coincide_con_medida(400, "Shampoo 400ml"))
        self.assertFalse(coincide_con_medida(55, "Pan 567 g"))

    def test_sanear_par_descarta_referencia_gramaje(self):
        actual, anterior, motivo = sanear_par_precios(55, 567, "Pan blanco Bimbo 567 g")
        self.assertEqual((actual, anterior, motivo), (55.0, None, "anterior_igual_a_gramaje"))
        actual, anterior, motivo = sanear_par_precios(55, "567g", "Pan")
        self.assertEqual((anterior, motivo), (None, "anterior_no_numerico"))
        # una rebaja real se respeta
        self.assertEqual(sanear_par_precios(500, 1000, "TV 55 pulgadas")[1], 1000.0)


class ParserTests(unittest.TestCase):
    def _payload(self, was, name="Arroz Morelos 875g"):
        return {"data": {"search": {"products": [{
            "id": "1", "name": name, "canonicalUrl": "/ip/arroz/1",
            "priceInfo": {"currentPrice": {"price": 32.5}, "wasPrice": {"price": was},
                          "priceString": "875g"},
        }]}}}

    def test_walmart_no_toma_gramaje_como_was_price(self):
        self.assertEqual(WalmartGraphQLParser().parsear_respuesta_busqueda(self._payload(875)), [])
        self.assertEqual(WalmartGraphQLParser().parsear_respuesta_busqueda(self._payload("875g")), [])

    def test_walmart_rebaja_real_si_pasa(self):
        rows = WalmartGraphQLParser().parsear_respuesta_busqueda(self._payload(80.0, "Arroz Morelos"))
        self.assertEqual(rows[0]["descuento"], 59)

    def test_walmart_online_termina_en_01_sin_rebaja_no_es_candidato(self):
        payload = {"data": {"search": {"products": [{
            "id": "2", "name": "Producto", "canonicalUrl": "/ip/p/2",
            "priceInfo": {"currentPrice": {"price": 849.01}}}]}}}
        self.assertEqual(WalmartGraphQLParser().parsear_respuesta_busqueda(payload), [])

    def test_extract_products_no_usa_gramaje_como_referencia(self):
        payload = {"products": [{"name": "Leche 1kg", "url": "/p/1", "price": 20, "listPrice": "1kg"}]}
        row = extract_products(payload, "https://www.chedraui.com.mx", "Chedraui")[0]
        self.assertIsNone(row["precio_anterior"])


class TerminacionesTests(unittest.TestCase):
    def test_online_no_suma_30(self):
        item = {"tienda": "Walmart MX", "titulo": "Licuadora", "precio_actual": 849.01,
                "precio_anterior": 1000.0, "url": "https://www.walmart.com.mx/ip/x/1"}
        self.assertNotIn("liquidacion_terminacion_centavos", analizar_precio_extremo(item)["senales"])
        self.assertNotIn("liquidacion_centavos_fisica", evaluate_product(item)["indicadores"])

    def test_fisica_si_suma_30(self):
        item = {"tienda": "Walmart MX", "titulo": "Licuadora", "precio_actual": 849.01,
                "tipo_fuente": "FISICA", "url": "https://www.walmart.com.mx/ip/x/1"}
        self.assertIn("liquidacion_centavos_fisica", evaluate_product(item)["indicadores"])
        self.assertTrue(senal_terminacion_liquidacion(849.02, "Bodega Aurrera", fisica=True))
        self.assertFalse(senal_terminacion_liquidacion(849.02, "Bodega Aurrera"))


class MonitorTests(unittest.TestCase):
    def test_calcular_datos_ignora_referencia_gramaje_e_historial_contaminado(self):
        item = {"tienda": "Walmart MX", "titulo": "Pan blanco Bimbo 567 g",
                "precio_actual": 55.0, "precio_anterior": 567.0}
        self.assertEqual(mo.calcular_datos(item), (55.0, 0.0, 0))
        self.assertEqual(mo.calcular_datos({**item, "precio_anterior": None},
                                           {"precio_maximo": 567.0}), (55.0, 0.0, 0))

    def test_historial_contaminado_se_repara(self):
        h = {"k": {"titulo": "Cereal 860 g", "precio_actual": 95.0, "precio_maximo": 860.0}}
        self.assertEqual(mo._sanear_historial(h)["k"]["precio_maximo"], 95.0)


class GoogleProxyTests(unittest.TestCase):
    def _scraper(self, payload, status=200):
        s = GoogleProxyScraper(url="https://script.google.com/macros/s/FAKE/exec")
        resp = MagicMock(status_code=status)
        resp.json.return_value = payload
        s.get = MagicMock(return_value=resp)
        return s

    def test_sin_url_not_configured(self):
        s = GoogleProxyScraper(url="")
        self.assertEqual(s.discover(), [])
        self.assertEqual(s.last_status["state"], "not_configured")

    def test_usa_allow_redirects_y_normaliza(self):
        payload = {"ofertas": [
            {"tienda": "walmart", "nombre": "TV 50", "url": "/ip/tv/1", "precio": 4000, "precio_anterior": 9000, "descuento": 99},
            {"tienda": "Bodega Aurrera", "name": "Arroz 875g", "url": "/ip/arroz/2", "price": 30, "listPrice": 875},
            {"tienda": "Soriana", "title": "Atún", "link": "https://www.soriana.com/atun/1.html", "price": "$10.50"},
        ]}
        s = self._scraper(payload)
        rows = s.discover()
        self.assertTrue(s.get.call_args.kwargs["allow_redirects"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["tienda"], "Walmart MX")
        self.assertEqual(rows[0]["url"], "https://www.walmart.com.mx/ip/tv/1")
        self.assertEqual(rows[1]["precio_anterior"], None)          # 875 era el gramaje
        self.assertEqual(rows[2]["precio_actual"], 10.5)
        self.assertTrue(all(r["tipo_fuente"] == "ONLINE" for r in rows))
        self.assertNotIn("descuento", rows[0])                       # no se hereda el del proxy

    def test_formato_anidado_data_search_products(self):
        payload = {"walmart": {"data": {"search": {"products": [{
            "id": "nested-1", "name": "Televisión 50 pulgadas",
            "canonicalUrl": "/ip/television/123", "priceInfo": {
                "currentPrice": {"price": 4000}, "wasPrice": {"price": 9000}
            }
        }]}}}}
        s = self._scraper(payload)
        rows = s.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tienda"], "Walmart MX")
        self.assertEqual(rows[0]["precio_actual"], 4000.0)
        self.assertEqual(rows[0]["precio_anterior"], 9000.0)
        self.assertEqual(s.last_status["state"], "ok")

    def test_formato_por_tienda_y_json_invalido(self):
        s = self._scraper({"walmart": [{"name": "A", "url": "/ip/a/1", "price": 5}],
                           "mercadolibre": [{"title": "B", "permalink": "https://articulo.mercadolibre.com.mx/MLM-1-b", "price": 7}]})
        self.assertEqual({r["tienda"] for r in s.discover()}, {"Walmart MX", "Mercado Libre MX"})
        s2 = GoogleProxyScraper(url="https://x.test/exec")
        resp = MagicMock(status_code=200); resp.json.side_effect = ValueError()
        s2.get = MagicMock(return_value=resp)
        self.assertEqual(s2.discover(), [])
        self.assertEqual(s2.last_status["state"], "invalid_response")


if __name__ == "__main__":
    unittest.main()
