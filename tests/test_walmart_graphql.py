import unittest

from scrapers.walmart_graphql import WalmartGraphQLParser
from scrapers.bodega_graphql import BodegaGraphQLParser

PAYLOAD = {
    "data": {
        "search": {
            "products": [
                {
                    "id": "123",
                    "name": "Pantalla Smart TV Ejemplo",
                    "brand": "Marca",
                    "canonicalUrl": "/ip/pantalla-smart-tv/123",
                    "priceInfo": {
                        "currentPrice": {"price": 849.01},
                        "wasPrice": {"price": 1699.00},
                    },
                },
                {
                    "id": "124",
                    "name": "Producto normal",
                    "brand": "Generica",
                    "canonicalUrl": "/ip/producto/124",
                    "priceInfo": {
                        "currentPrice": {"price": 849.00},
                        "wasPrice": {"price": 1000.00},
                    },
                },
            ]
        }
    }
}


class WalmartGraphQLTests(unittest.TestCase):
    def test_detecta_liquidacion_por_rebaja_matematica(self):
        rows = WalmartGraphQLParser().parsear_respuesta_busqueda(PAYLOAD)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["precio_actual"], 849.01)
        self.assertEqual(row["precio_anterior"], 1699.0)
        self.assertEqual(row["descuento"], 50)
        self.assertFalse(row["es_bomba"])  # 50 % no es bomba; las terminaciones .01 ya no puntúan online
        self.assertEqual(row["tienda"], "Walmart MX")

    def test_bodega_cambia_dominio_y_tienda(self):
        rows = BodegaGraphQLParser().parsear_respuesta_busqueda(PAYLOAD)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["url"].startswith("https://www.bodegaaurrera.com.mx/"))
        self.assertEqual(rows[0]["tienda"], "Bodega Aurrera")


    def test_bodega_parser_keeps_current_and_reference_pair(self):
        parser = BodegaGraphQLParser()
        payload = {
            "data": {"search": {"products": [{
                "id": "BA-1",
                "name": "Licuadora de prueba",
                "canonicalUrl": "/ip/licuadora/BA-1",
                "priceInfo": {
                    "currentPrice": {"price": 100},
                    "wasPrice": {"price": 200},
                },
            }]}}
        }
        rows = parser.parsear_respuesta_busqueda(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tienda"], "Bodega Aurrera")
        self.assertEqual(rows[0]["precio_actual"], 100.0)
        self.assertEqual(rows[0]["precio_anterior"], 200.0)
        self.assertEqual(rows[0]["descuento"], 50)

    def test_walmart_and_bodega_reject_boolean_current_price(self):
        payload = {
            "data": {"search": {"products": [{
                "id": "BAD-1",
                "name": "Producto de prueba",
                "canonicalUrl": "/ip/producto/BAD-1",
                "priceInfo": {
                    "currentPrice": {"price": True},
                    "wasPrice": {"price": 100},
                },
            }]}}
        }
        self.assertEqual(WalmartGraphQLParser().parsear_respuesta_busqueda(payload), [])
        self.assertEqual(BodegaGraphQLParser().parsear_respuesta_busqueda(payload), [])

if __name__ == "__main__":
    unittest.main()
