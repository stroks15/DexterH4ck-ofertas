import unittest

from core.offer_identity import canonical_url, deduplicate_candidates, history_key, identity_keys


class OfferIdentityTests(unittest.TestCase):
    def test_tracking_params_do_not_change_url_identity(self):
        a = {"tienda": "Amazon MX", "titulo": "Audífonos JBL", "url": "https://www.amazon.com.mx/dp/B0ABC12345?tag=oferta-20&utm_source=x"}
        b = {"tienda": "Amazon MX", "titulo": "Audífonos JBL", "url": "https://www.amazon.com.mx/dp/B0ABC12345"}
        self.assertEqual(canonical_url(a["url"]), canonical_url(b["url"]))
        self.assertTrue(identity_keys(a) & identity_keys(b))

    def test_same_product_from_different_sources_is_deduped(self):
        items = [
            {"tienda": "Walmart MX", "titulo": "Producto de prueba", "url": "https://www.walmart.com.mx/ip/producto/123456789", "origen_link": "api"},
            {"tienda": "Walmart MX", "titulo": "Producto de prueba", "url": "https://www.walmart.com.mx/ip/producto/123456789?utm_source=x", "origen_link": "telegram"},
        ]
        result, duplicates = deduplicate_candidates(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(duplicates, 1)

    def test_history_key_is_stable(self):
        a = {"tienda": "Liverpool", "titulo": "Playera Aeropostale", "url": "https://www.liverpool.com.mx/tienda/pdp/playera/1203990274"}
        b = {"tienda": "Liverpool", "titulo": "Playera Aeropostale", "url": "https://www.liverpool.com.mx/tienda/pdp/playera/1203990274?utm_campaign=x"}
        self.assertEqual(history_key(a), history_key(b))


if __name__ == "__main__":
    unittest.main()
