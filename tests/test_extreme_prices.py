import unittest
from core.extreme_liquidation import porcentaje_descuento, es_liquidacion_95_99, es_producto_oficial
from scrapers.liquidaciones_oficiales import _offer_prices

class TestExtremePrices(unittest.TestCase):
    def test_95_percent(self):
        self.assertEqual(porcentaje_descuento(50, 1000), 95.0)
        self.assertTrue(es_liquidacion_95_99(50, 1000))
    def test_extracts_current_and_reference_prices(self):
        current, reference = _offer_prices({'price': '10.00', 'listPrice': '1000.00'})
        self.assertEqual(current, 10.0)
        self.assertEqual(reference, 1000.0)

    def test_official_host(self):
        self.assertTrue(es_producto_oficial('https://www.walmart.com.mx/ip/item', 'Walmart MX'))
        self.assertFalse(es_producto_oficial('https://walmart.com.mx.example.invalid/item', 'Walmart MX'))

if __name__ == '__main__':
    unittest.main()
