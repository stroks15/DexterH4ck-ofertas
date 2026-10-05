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
    def test_detecta_liquidacion_y_centavos_01(self):
        rows = WalmartGraphQLParser().parsear_respuesta_busqueda(PAYLOAD)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["precio_actual"], 849.01)
        self.assertEqual(row["precio_anterior"], 1699.0)
        self.assertEqual(row["descuento"], 50)
        self.assertTrue(row["es_bomba"])
        self.assertEqual(row["tienda"], "Walmart MX")

    def test_bodega_cambia_dominio_y_tienda(self):
        rows = BodegaGraphQLParser().parsear_respuesta_busqueda(PAYLOAD)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["url"].startswith("https://www.bodegaaurrera.com.mx/"))
        self.assertEqual(rows[0]["tienda"], "Bodega Aurrera")


if __name__ == "__main__":
    unittest.main()
