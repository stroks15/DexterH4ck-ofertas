import unittest
from core.network_json import discount, extract_products


class NetworkJsonTests(unittest.TestCase):
    def test_extracts_nested_product_and_prices(self):
        payload = {
            "data": {
                "products": [
                    {
                        "productId": "ABC123",
                        "productName": "Pantalla 55 pulgadas",
                        "priceInfo": {"currentPrice": {"price": "9999.00"}, "wasPrice": {"price": "19999.00"}},
                        "canonicalUrl": "/p/pantalla-55",
                    }
                ]
            }
        }
        rows = extract_products(payload, "https://tienda.example.com", "Tienda")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product_id"], "ABC123")
        self.assertEqual(rows[0]["precio_actual"], 9999.0)
        self.assertEqual(rows[0]["precio_anterior"], 19999.0)
        self.assertEqual(rows[0]["descuento"], 50)

    def test_ignores_reference_below_current(self):
        rows = extract_products(
            [{"id": "1", "name": "Producto", "price": 100, "listPrice": 90, "url": "/p/1"}],
            "https://example.com",
            "Tienda",
        )
        self.assertEqual(rows[0]["precio_anterior"], None)
        self.assertEqual(rows[0]["descuento"], 0)

    def test_discount_is_bounded(self):
        self.assertEqual(discount(10, 100), 90)
        self.assertEqual(discount(10, 1000), 99)
        self.assertEqual(discount(100, 90), 0)


    def test_scraper_api_manager_falls_back_direct_after_budget(self):
        import os
        from unittest.mock import Mock, patch
        from core.scraper_base import ScraperApiManager

        manager = ScraperApiManager()
        with patch.dict(os.environ, {
            "SCRAPER_API_ENABLED": "true",
            "SCRAPER_API_KEY": "test-key",
            "SCRAPER_API_ENDPOINT": "https://proxy.example.test/",
            "MAX_PROXY_CREDITS_PER_CYCLE": "1",
        }, clear=False):
            manager.reset_cycle()
            session = Mock()
            manager.request(session, "https://shop.example.test/products", timeout=2)
            manager.request(session, "https://shop.example.test/products", timeout=2)
            self.assertEqual(session.get.call_count, 2)
            self.assertEqual(session.get.call_args_list[0].args[0], "https://proxy.example.test/")
            self.assertEqual(session.get.call_args_list[1].args[0], "https://shop.example.test/products")
            self.assertEqual(manager._credits_used, 1)
        manager.reset_cycle()

    def test_vtex_catalog_adapter_normalizes_native_product(self):
        from unittest.mock import Mock, patch
        from scrapers.api_stores import VtexCatalogAdapter

        class DemoVtex(VtexCatalogAdapter):
            store = "Tienda demo"
            endpoint_env = "DEMO_VTEX_ENDPOINT"
            home = "https://shop.example.test"

        response = Mock()
        response.status_code = 200
        response.json.return_value = [{
            "productId": "sku-1",
            "productName": "Producto de prueba",
            "link": "/producto-de-prueba/p",
            "items": [{
                "itemId": "sku-1",
                "sellers": [{
                    "sellerId": "1",
                    "commertialOffer": {
                        "AvailableQuantity": 2,
                        "Price": 50,
                        "ListPrice": 100
                    }
                }]
            }]
        }]
        adapter = DemoVtex(endpoint="https://shop.example.test")
        with patch.object(adapter, "get", return_value=response):
            rows = adapter.discover()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tienda"], "Tienda demo")
        self.assertEqual(rows[0]["precio_actual"], 50)
        self.assertEqual(rows[0]["precio_anterior"], 100)
        self.assertEqual(rows[0]["descuento"], 50)
        self.assertEqual(rows[0]["tipo_fuente"], "ONLINE")
        self.assertEqual(adapter.last_status["state"], "ok")


if __name__ == "__main__":
    unittest.main()
