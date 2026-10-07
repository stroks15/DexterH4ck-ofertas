import unittest
from core.network_json import discount, extract_products

class NetworkJsonTests(unittest.TestCase):
    def test_extracts_nested_product_and_prices(self):
        payload={"data":{"products":[{"productId":"ABC123","productName":"Pantalla 55 pulgadas","priceInfo":{"currentPrice":{"price":"9999.00"},"wasPrice":{"price":"19999.00"}},"canonicalUrl":"/p/pantalla-55"}]}}
        rows=extract_products(payload,"https://tienda.example.com","Tienda")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["product_id"],"ABC123")
        self.assertEqual(rows[0]["precio_actual"],9999.0)
        self.assertEqual(rows[0]["precio_anterior"],19999.0)
        self.assertEqual(rows[0]["descuento"],50)

    def test_ignores_reference_below_current(self):
        rows=extract_products([{"id":"1","name":"Producto","price":100,"listPrice":90,"url":"/p/1"}],"https://example.com","Tienda")
        self.assertEqual(rows[0]["precio_anterior"],None)
        self.assertEqual(rows[0]["descuento"],0)

    def test_discount_is_bounded(self):
        self.assertEqual(discount(10,100),90)
        self.assertEqual(discount(10,1000),99)
        self.assertEqual(discount(100,90),0)

if __name__=="__main__":
    unittest.main()
