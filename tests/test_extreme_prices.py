import unittest
from core.extreme_liquidation import porcentaje_descuento, es_liquidacion_95_99, es_producto_oficial
from core.liquidation_engine import calculate_discount
from scrapers.liquidaciones_oficiales import _offer_prices


class TestExtremePrices(unittest.TestCase):
    def test_95_percent(self):
        self.assertEqual(porcentaje_descuento(50, 1000), 95.0)
        self.assertTrue(es_liquidacion_95_99(50, 1000))

    def test_extracts_current_and_reference_prices(self):
        current, reference = _offer_prices({"price": "10.00", "listPrice": "1000.00"})
        self.assertEqual(current, 10.0)
        self.assertEqual(reference, 1000.0)

    def test_extreme_real_discount_is_capped_to_monitor_range(self):
        # $1 sobre $200 es 99.5%; round() daría 100 y el filtro 50–99 lo perdería.
        self.assertEqual(calculate_discount(200, 1), 99)
        self.assertEqual(calculate_discount(1000, 1), 99)

    def test_physical_scraper_keeps_prices_below_one_peso(self):
        from scrapers.tiendas_fisicas import precios

        self.assertEqual(precios("Liquidación $0.50 antes $25.00"), [0.5, 25.0])

    def test_official_host(self):
        self.assertTrue(es_producto_oficial("https://www.walmart.com.mx/ip/item", "Walmart MX"))
        self.assertFalse(es_producto_oficial("https://walmart.com.mx.example.invalid/item", "Walmart MX"))


if __name__ == "__main__":
    unittest.main()
