import unittest
from unittest.mock import patch

import monitor_ofertas as mon


def _item(precio, anterior, ident="123456789"):
    return {
        "tienda": "Walmart MX", "titulo": "Pantalla Smart TV Ejemplo 55 pulgadas",
        "url": f"https://www.walmart.com.mx/ip/pantalla/{ident}",
        "precio_actual": precio, "precio_anterior": anterior, "origen_link": "api",
    }


class Bomb95to99SupabaseTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("core.extreme_liquidation.verificar_precio_producto", return_value=(False, "mock"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_blocked_when_supabase_says_already_sent(self):
        with patch.object(mon.supabase_history, "ya_enviada", return_value=True):
            self.assertEqual(mon.procesar_candidatos([_item(50, 1000)], {}), [])

    def test_allowed_when_supabase_says_new(self):
        with patch.object(mon.supabase_history, "ya_enviada", return_value=False):
            alertas = mon.procesar_candidatos([_item(50, 1000)], {})
        self.assertEqual(len(alertas), 1)

    def test_supabase_unknown_falls_back_to_local_history(self):
        with patch.object(mon.supabase_history, "ya_enviada", return_value=None):
            alertas = mon.procesar_candidatos([_item(50, 1000)], {})
            self.assertEqual(len(alertas), 1)                       # nada local -> pasa
            historial = {alertas[0]["clave"]: {"precio_alertado": 50}}
            self.assertEqual(mon.procesar_candidatos([_item(50, 1000)], historial), [])  # local -> bloquea

    def test_below_95_does_not_query_supabase(self):
        with patch.object(mon.supabase_history, "ya_enviada") as consulta:
            mon.procesar_candidatos([_item(100, 1000)], {})          # 90 %
            consulta.assert_not_called()

    def test_bomb_prefix_is_mandatory_for_95_to_99(self):
        alerta = {"item": _item(40, 1000), "bomba": False, "actual": 40.0, "referencia": 1000.0,
                  "descuento": 96, "evaluacion": {}}
        self.assertTrue(mon.formatear_alerta(alerta).startswith("💣"))
        alerta["descuento"] = 60
        self.assertNotIn("💣", mon.formatear_alerta(alerta))

    def test_send_registers_only_95_to_99_in_supabase(self):
        extrema = {"clave": "k1", "item": _item(50, 1000, "111"), "bomba": True, "actual": 50.0,
                   "referencia": 1000.0, "descuento": 95, "evaluacion": {}}
        normal = {"clave": "k2", "item": _item(500, 1000, "222"), "bomba": False, "actual": 500.0,
                  "referencia": 1000.0, "descuento": 50, "evaluacion": {}}
        with patch.object(mon, "enviar_telegram", return_value=True), \
                patch.object(mon.supabase_history, "registrar_envio") as registrar:
            enviadas, fallidas = mon.enviar_alertas([extrema, normal], {})
        self.assertEqual((enviadas, fallidas), (2, 0))
        registrar.assert_called_once()
        self.assertEqual(registrar.call_args[0][2], 95)

    def test_failed_telegram_send_is_not_registered(self):
        extrema = {"clave": "k1", "item": _item(50, 1000), "bomba": True, "actual": 50.0,
                   "referencia": 1000.0, "descuento": 97, "evaluacion": {}}
        with patch.object(mon, "enviar_telegram", return_value=False), \
                patch.object(mon.supabase_history, "registrar_envio") as registrar:
            mon.enviar_alertas([extrema], {})
        registrar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
