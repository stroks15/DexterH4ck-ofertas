import unittest
from unittest.mock import patch

from core.offer_identity import history_key
from core import supabase_history
from core.liquidation_engine import calculate_discount, score_product
from monitor_ofertas import MIN_DESCUENTO, MAX_DESCUENTO, calcular_datos, enviar_alertas, validar_y_filtrar_bomba


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
        self.assertEqual(reference, 1000)
        self.assertEqual(discount, 90)


    def test_alerta_critica_persiste_json_y_supabase_tras_envio(self):
        item = {
            "tienda": "Walmart MX",
            "titulo": "Televisión de prueba",
            "url": "https://www.walmart.com.mx/ip/television/123456",
            "precio_actual": 4,
            "precio_anterior": 100,
            "descuento": 96,
        }
        clave = history_key(item)
        alerta = {
            "clave": clave,
            "item": item,
            "actual": 4,
            "referencia": 100,
            "descuento": 96,
            "evaluacion": {"puntuacion": 96, "indicadores": []},
            "bomba": True,
        }
        historial = {clave: {"precio_alertado": None}}
        with patch("monitor_ofertas.enviar_telegram", return_value=True), \\
             patch("monitor_ofertas.guardar_historial") as guardar, \\
             patch.object(supabase_history, "registrar_envio", return_value=True) as registrar:
            enviadas, fallidas = enviar_alertas([alerta], historial)
        self.assertEqual((enviadas, fallidas), (1, 0))
        self.assertEqual(historial[clave]["precio_alertado"], 4)
        guardar.assert_called_once_with(historial)
        registrar.assert_called_once_with(clave, item, 96)

    def test_bomba_usa_exactamente_history_key(self):
        item = {
            "tienda": "Walmart MX",
            "titulo": "Televisión de prueba",
            "url": "https://www.walmart.com.mx/ip/television/123456",
            "precio_actual": 4,
            "precio_anterior": 100,
            "descuento": 96,
        }
        clave = history_key(item)
        with patch.object(supabase_history, "ya_enviada", return_value=False):
            # Una llave heredada diferente no debe bloquear por coincidencia accidental.
            self.assertTrue(
                validar_y_filtrar_bomba(item, {"walmart_123456": {"precio_alertado": 4}})
            )
            # La llave canónica sí bloquea una oferta ya alertada.
            self.assertFalse(
                validar_y_filtrar_bomba(item, {clave: {"precio_alertado": 4}})
            )


if __name__ == "__main__":
    unittest.main()
