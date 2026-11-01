import unittest

from scrapers.api_stores import VtexCatalogAdapter


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

    def test_boolean_stock_is_not_selected(self):
        scraper = VtexCatalogAdapter.__new__(VtexCatalogAdapter)
        product = {"items": [{"sellers": [
            {"commertialOffer": {"AvailableQuantity": True, "Price": 1, "ListPrice": 100}},
        ]}]}
        self.assertEqual(scraper._prices(product), (None, None))


if __name__ == "__main__":
    unittest.main()
