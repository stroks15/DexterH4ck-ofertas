import unittest
from unittest.mock import patch

from scrapers.hybrid_discovery import extract_structured_html


class HybridDiscoveryTests(unittest.TestCase):
    def test_extracts_json_ld_product_with_evidence(self):
        html = '''<script type="application/ld+json">
        {"@type":"Product","name":"Audífonos Demo","sku":"sku-1",
         "url":"/p/audifonos-demo","offers":{"price":"499.00",
         "priceCurrency":"MXN","availability":"https://schema.org/InStock"}}
        </script>'''
        rows = extract_structured_html(html, "https://shop.example.test", "Tienda")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["titulo"], "Audífonos Demo")
        self.assertEqual(rows[0]["precio_actual"], 499.0)
        self.assertEqual(rows[0]["product_id"], "sku-1")
        self.assertEqual(rows[0]["origen_link"], "network_json")

    def test_does_not_infer_price_from_unstructured_text(self):
        html = "<h1>Producto 875g desde $999</h1><a href='/p/demo'>Ficha</a>"
        self.assertEqual(extract_structured_html(html, "https://shop.example.test", "Tienda"), [])

    def test_invalid_json_ld_is_ignored(self):
        html = '<script type="application/ld+json">not-json</script>'
        self.assertEqual(extract_structured_html(html, "https://shop.example.test", "Tienda"), [])


if __name__ == "__main__":
    unittest.main()
