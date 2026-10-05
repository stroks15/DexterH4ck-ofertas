import unittest

from scrapers.mercado_libre_api import _discount
from scrapers.feeds_comunidad_api import _store


class MercadoLibreAndFeedTests(unittest.TestCase):
    def test_descuento_real_50_a_99(self):
        self.assertEqual(_discount(500, 1000), 50)
        self.assertEqual(_discount(100, 1000), 90)
        self.assertEqual(_discount(0, 1000), 0)
        self.assertEqual(_discount(1000, 1000), 0)

    def test_tiendas_del_feed(self):
        self.assertEqual(_store("Amazon: producto en oferta"), "Amazon MX")
        self.assertEqual(_store("Coppel remate"), "Coppel")
        self.assertEqual(_store("Liquidación física Walmart"), "Walmart MX")


if __name__ == "__main__":
    unittest.main()
