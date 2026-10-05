import unittest

from monitor_ofertas import _candidato_de_tienda_objetivo
from scrapers.api_stores import api_first_scrapers


class FlowIntegrationTests(unittest.TestCase):
    def test_fuentes_fisicas_y_telegram_no_se_descartan(self):
        self.assertTrue(_candidato_de_tienda_objetivo({
            "tienda": "Coppel Remate Los Reyes",
            "tipo_fuente": "FISICA",
            "url": "https://example.com/evidencia",
        }))
        self.assertTrue(_candidato_de_tienda_objetivo({
            "tienda": "Walmart",
            "origen_link": "Telegram",
            "url": "https://www.walmart.com.mx/ip/producto/123",
        }))

    def test_todas_las_tiendas_online_forman_parte_de_api_first(self):
        stores = {scraper.store for scraper in api_first_scrapers()}
        expected = {
            "Walmart MX", "Bodega Aurrera", "Chedraui", "Soriana",
            "Liverpool", "Amazon MX", "Mercado Libre MX", "Coppel",
            "Suburbia", "Oferstock",
        }
        self.assertTrue(expected.issubset(stores))


if __name__ == "__main__":
    unittest.main()
