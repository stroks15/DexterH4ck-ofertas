import unittest
from unittest.mock import Mock, patch

from scrapers.api_stores import VtexCatalogAdapter
from scrapers.vtex_stores import VtexStoresScraper


class TestVtexFinancialValidation(unittest.TestCase):
    def test_price_parser_rejects_boolean_and_non_financial_text(self):
        self.assertIsNone(VtexCatalogAdapter._positive_price(True))
        self.assertIsNone(VtexCatalogAdapter._positive_price(False))
        self.assertIsNone(VtexCatalogAdapter._positive_price("875g"))
        self.assertIsNone(VtexCatalogAdapter._positive_price("4 pzas"))
        self.assertIsNone(VtexCatalogAdapter._positive_price("precio 1.79"))
        self.assertIsNone(VtexCatalogAdapter._positive_price(float("inf")))

    def test_price_parser_accepts_numeric_and_currency_amount(self):
        self.assertEqual(VtexCatalogAdapter._positive_price(1299.0), 1299.0)
        self.assertEqual(VtexCatalogAdapter._positive_price("$1,299.00"), 1299.0)

    def test_seller_without_stock_is_not_selected(self):
        scraper = VtexCatalogAdapter.__new__(VtexCatalogAdapter)
        product = {"items": [{"sellers": [
            {"commertialOffer": {"AvailableQuantity": 0, "Price": 1, "ListPrice": 100}},
            {"commertialOffer": {"AvailableQuantity": 4, "Price": 80, "ListPrice": 100}},
        ]}]}
        self.assertEqual(scraper._prices(product), (80.0, 100.0))

    def test_chedraui_vtex_rejects_boolean_and_non_finite_prices(self):
        product = {"items": [{"sellers": [
            {"commertialOffer": {"AvailableQuantity": 3, "Price": True, "ListPrice": 100}},
            {"commertialOffer": {"AvailableQuantity": 3, "Price": float("nan"), "ListPrice": 100}},
            {"commertialOffer": {"AvailableQuantity": 3, "Price": float("inf"), "ListPrice": 100}},
            {"commertialOffer": {"AvailableQuantity": 3, "Price": 50, "ListPrice": 100}},
        ]}]}
        self.assertEqual(VtexStoresScraper._offer(product), (50.0, 100.0))

    def test_chedraui_vtex_rejects_reference_that_is_product_grammage(self):
        scraper = VtexStoresScraper(endpoint="https://www.chedraui.com.mx/vtex")
        rows = scraper.parse([{
            "productId": "CEREAL-500",
            "productName": "Cereal de maíz 500 g",
            "description": "Cereal de maíz 500 g",
            "link": "/cereal-500/p",
            "items": [{"sellers": [{"commertialOffer": {
                "AvailableQuantity": 2, "Price": 50, "ListPrice": 500
            }}]}],
        }])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["precio_anterior"])
        self.assertEqual(rows[0]["descuento"], 0)

    def test_chedraui_vtex_base_path_is_normalized_to_catalog_search(self):
        scraper = VtexStoresScraper(endpoint="https://www.chedraui.com.mx/vtex")
        response = Mock(status_code=200)
        response.json.return_value = []
        with patch.object(scraper, "get", return_value=response) as get:
            state, _ = scraper._pedir_pagina({"_from": "0", "_to": "49"})
        self.assertEqual(state, "ok")
        self.assertEqual(
            get.call_args.args[0],
            "https://www.chedraui.com.mx/api/catalog_system/pub/products/search",
        )

    def test_boolean_stock_is_not_selected(self):
        scraper = VtexCatalogAdapter.__new__(VtexCatalogAdapter)
        product = {"items": [{"sellers": [
            {"commertialOffer": {"AvailableQuantity": True, "Price": 1, "ListPrice": 100}},
        ]}]}
        self.assertEqual(scraper._prices(product), (None, None))


if __name__ == "__main__":
    unittest.main()
