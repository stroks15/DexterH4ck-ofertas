import unittest

from core.liquidation_engine import calculate_discount, score_product
from monitor_ofertas import MIN_DESCUENTO, MAX_DESCUENTO, calcular_datos


class TestMonitorRules(unittest.TestCase):
    def test_publication_range_is_50_99(self):
        self.assertEqual(MIN_DESCUENTO, 50)
        self.assertEqual(MAX_DESCUENTO, 99)

    def test_discount_calculation(self):
        self.assertEqual(calculate_discount(1000, 500), 50)
        self.assertEqual(calculate_discount(1000, 100), 90)

    def test_discount_component_scales_to_100(self):
        self.assertEqual(score_product({"titulo": "producto"}, 0)["componente_descuento"], 5)
        self.assertEqual(score_product({"titulo": "producto"}, 99)["componente_descuento"], 100)

    def test_history_reference_is_conservative(self):
        actual, reference, discount = calcular_datos(
            {"precio_actual": 100, "precio_anterior": 1000},
            {"precio_maximo": 400},
        )
        self.assertEqual(reference, 400)
        self.assertEqual(discount, 75)


if __name__ == "__main__":
    unittest.main()
