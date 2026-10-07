import unittest
from core.offer_identity import canonical_store
from scrapers.tiendas_mexico import es_url_producto

class StoreDiscoveryRegressionTests(unittest.TestCase):
    def test_suburbia_pdp_is_product(self):
        url = "https://www.suburbia.com.mx/tienda/pdp/Top/SB5014091999"
        self.assertTrue(es_url_producto(url, "https://www.suburbia.com.mx/"))

    def test_store_aliases_are_canonical(self):
        self.assertEqual(canonical_store("Amazon MX"), "amazon")
        self.assertEqual(canonical_store("Mercado Libre MX"), "mercadolibre")

    def test_bodega_despensa_product_url(self):
        self.assertTrue(es_url_producto(
            "https://despensa.bodegaaurrera.com.mx/ip/Producto/00750231572066",
            "https://www.bodegaaurrera.com.mx/",
        ))

if __name__ == "__main__":
    unittest.main()
