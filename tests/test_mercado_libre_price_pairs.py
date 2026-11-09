import unittest
from unittest.mock import Mock

from scrapers.mercado_libre_api import MercadoLibreApi


class MercadoLibrePricePairTests(unittest.TestCase):
    def _client(self):
        client = MercadoLibreApi()
        client.token = "test-token"
        client.delay = 0
        return client

    def test_promotion_current_and_regular_price_come_from_same_row(self):
        client = self._client()
        response = Mock(status_code=200)
        response.json.return_value = {
            "prices": [
                {"type": "promotion", "amount": 1.10, "regular_amount": 2.89},
                # Esta segunda campaña no tiene un regular_amount superior a su
                # precio. No debe combinarse con la referencia de la primera.
                {"type": "promotion", "amount": 2.50, "regular_amount": 2.00},
            ]
        }
        client._get = Mock(return_value=response)

        self.assertEqual(client._price_reference("MLM123"), (1.10, 2.89))

    def test_promotion_without_reference_does_not_borrow_standard_price(self):
        client = self._client()
        response = Mock(status_code=200)
        response.json.return_value = {
            "prices": [
                {"type": "promotion", "amount": 1.10, "regular_amount": None},
                {"type": "standard", "amount": 2.89},
            ]
        }
        client._get = Mock(return_value=response)

        self.assertEqual(client._price_reference("MLM123"), (1.10, None))

    def test_prices_endpoint_without_reference_does_not_reuse_search_reference(self):
        client = self._client()
        client.max_price_lookups = 1
        client.search = Mock(return_value=[{
            "id": "MLM123",
            "title": "Producto de prueba",
            "price": 1.10,
            "original_price": 2.89,
            "permalink": "https://articulo.mercadolibre.com.mx/MLM-123-producto",
        }])
        client._price_reference = Mock(return_value=(1.10, None))

        # Si se mezclara el original_price de /search con el amount de /prices,
        # aparecería una oferta falsa del 62 %. La referencia queda desconocida.
        self.assertEqual(client.discover(), [])


if __name__ == "__main__":
    unittest.main()
