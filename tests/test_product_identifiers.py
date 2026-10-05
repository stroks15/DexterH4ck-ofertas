import unittest

from core.product_identifiers import (
    extract_amazon_context,
    extract_bodega_context,
    extract_coppel_context,
    extract_mercadolibre_id,
    extract_walmart_context,
    unwrap_affiliate_url,
)


class ProductIdentifierTests(unittest.TestCase):
    def test_walmart_upc_y_sucursal(self):
        data = extract_walmart_context("https://www.walmart.com.mx/ip/licuadora/00750229784055?wl13=2347")
        self.assertEqual(data["upc"], "00750229784055")
        self.assertEqual(data["store_id"], "2347")

    def test_bodega_upc(self):
        data = extract_bodega_context("https://www.bodegaaurrera.com.mx/ip/set/00019299540427")
        self.assertEqual(data["product_id"], "00019299540427")

    def test_coppel_afiliado_y_sku(self):
        affiliate = "https://ad.soicos.com/sclick?aid=53701&pid=12029&dl=https%3A%2F%2Fwww.coppel.com%2Fpdp%2Ftenis-aeropostale-ayun-para-hombre-pr-8909152"
        clean = unwrap_affiliate_url(affiliate)
        self.assertIn("coppel.com/pdp/", clean)
        self.assertEqual(extract_coppel_context(affiliate)["sku"], "8909152")

    def test_amazon_asin(self):
        self.assertEqual(extract_amazon_context("https://www.amazon.com.mx/dp/B0HLQZS8DJ?tag=x")["asin"], "B0HLQZS8DJ")

    def test_meli_id(self):
        self.assertEqual(extract_mercadolibre_id("https://www.mercadolibre.com.mx/MLM-123456789"), "MLM123456789")


if __name__ == "__main__":
    unittest.main()
