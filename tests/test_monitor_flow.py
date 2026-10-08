import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import monitor_ofertas as mon
from scrapers.api_stores import JsonEndpointAdapter, api_first_scrapers


def _item(precio, anterior, tienda="Walmart MX", ident="123456789"):
    return {
        "tienda": tienda, "titulo": "Pantalla Smart TV Ejemplo 55 pulgadas",
        "url": f"https://www.walmart.com.mx/ip/pantalla/{ident}",
        "precio_actual": precio, "precio_anterior": anterior, "origen_link": "api",
    }


class OrchestrationIsolationTests(unittest.TestCase):
    def test_failing_and_blocked_sources_do_not_stop_the_others(self):
        def boom():
            raise RuntimeError("fallo simulado de Walmart")

        fuentes = [
            mon.Fuente("api:Walmart MX", {"walmart"}, boom),
            mon.Fuente("api:Chedraui", {"chedraui"}, lambda: [_item(100, 1000, "Chedraui")]),
            mon.Fuente("api:Soriana", {"soriana"}, lambda: [_item(100, 1000, "Soriana")]),
        ]
        with patch.object(mon, "definir_fuentes", return_value=fuentes), \
                patch.dict(os.environ, {"PREFLIGHT_BLOCKED_STORES": "Soriana"}):
            candidatos = mon.ejecutar_orquestacion_paralela()
        estados = {f["fuente"]: f["estado"] for f in mon.ULTIMO_REPORTE_FUENTES["fuentes"]}
        self.assertEqual(estados["api:Walmart MX"], "error")           # el error NO se disfraza de ok
        self.assertEqual(estados["api:Chedraui"], "ok")
        self.assertEqual(estados["api:Soriana"], "skipped_preflight")  # solo ESA tienda se omite
        self.assertEqual(len(candidatos), 1)

    def test_declared_blocked_status_is_not_reported_as_success(self):
        fuente = mon.Fuente("api:X", set(), lambda: [], lambda: {"state": "rate_limited"})
        with patch.object(mon, "definir_fuentes", return_value=[fuente]):
            mon.ejecutar_orquestacion_paralela()
        self.assertEqual(mon.ULTIMO_REPORTE_FUENTES["fuentes"][0]["estado"], "rate_limited")


class AlertRulesTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("core.extreme_liquidation.verificar_precio_producto", return_value=(False, "mock"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_95_to_99_is_priority_bomb_and_sent_only_once(self):
        historial = {}
        alertas = mon.procesar_candidatos([_item(50, 1000)], historial)
        self.assertEqual(len(alertas), 1)
        self.assertTrue(alertas[0]["bomba"])
        self.assertIn("💣", mon.formatear_alerta(alertas[0]))
        historial[alertas[0]["clave"]]["precio_alertado"] = 50
        self.assertEqual(mon.procesar_candidatos([_item(50, 1000)], historial), [])

    def test_90_percent_with_valid_reference_is_bomb(self):
        alertas = mon.procesar_candidatos([_item(100, 1000)], {})
        self.assertEqual(len(alertas), 1)
        self.assertTrue(alertas[0]["bomba"])

    def test_below_min_discount_is_not_published(self):
        self.assertEqual(mon.procesar_candidatos([_item(600, 1000)], {}), [])

    def test_normal_offer_is_not_marked_bomb(self):
        alertas = mon.procesar_candidatos([_item(500, 1000)], {})
        self.assertEqual(len(alertas), 1)
        self.assertFalse(alertas[0]["bomba"])

    def test_invalid_url_is_rejected(self):
        item = _item(50, 1000)
        item["url"] = "https://www.walmart.com.mx/ip/null"
        with patch("core.ai_reparador.reparar_url", side_effect=lambda u, *a, **k: u):
            self.assertEqual(mon.procesar_candidatos([item], {}), [])


class TelegramTests(unittest.TestCase):
    def test_uses_real_api_host_and_reports_failure_honestly(self):
        respuesta = MagicMock(status_code=403)
        sesion = MagicMock()
        sesion.post.return_value = respuesta
        with patch.object(mon, "TELEGRAM_TOKEN", "TOKEN"), patch.object(mon, "TELEGRAM_CHAT_ID", "1"), \
                patch.object(mon.requests, "Session", return_value=sesion), patch.object(mon.time, "sleep"):
            self.assertFalse(mon.enviar_telegram("hola"))
        self.assertTrue(sesion.post.call_args[0][0].startswith("https://api.telegram.org/botTOKEN/"))

    def test_missing_credentials_raise(self):
        with patch.object(mon, "TELEGRAM_TOKEN", None):
            with self.assertRaises(RuntimeError):
                mon.enviar_telegram("hola")


class HistoryTests(unittest.TestCase):
    def test_unreadable_history_is_not_overwritten(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                open(mon.HISTORIAL_FILE, "w", encoding="utf-8").write("{roto")
                self.assertTrue(mon._historial_ilegible())
                open(mon.HISTORIAL_FILE, "w", encoding="utf-8").write('{"a": {"titulo": "x"}}')
                self.assertFalse(mon._historial_ilegible())
            finally:
                os.chdir(cwd)

    def test_guardar_historial_trims_without_crashing(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.object(mon, "MAX_HISTORIAL", 2):
                    mon.guardar_historial({"a": {"ultima_actualizacion": "1"}, "b": {"ultima_actualizacion": "3"},
                                           "c": {"ultima_actualizacion": "2"}})
                import json
                self.assertEqual(set(json.load(open(mon.HISTORIAL_FILE, encoding="utf-8"))), {"b", "c"})
            finally:
                os.chdir(cwd)


class ApiFirstAdaptersTests(unittest.TestCase):
    def test_unconfigured_endpoints_are_reported_not_failed(self):
        with patch.dict(os.environ, {}, clear=False):
            for var in ("SORIANA_API_ENDPOINT", "COPPEL_API_ENDPOINT", "SUBURBIA_API_ENDPOINT",
                        "AMAZON_API_ENDPOINT", "CHEDRAUI_VTEX_ENDPOINT", "LIVERPOOL_API_ENDPOINT",
                        "OFERSTOCK_API_ENDPOINT"):
                os.environ.pop(var, None)
            for scraper in api_first_scrapers():
                if scraper.store in ("Walmart MX", "Bodega Aurrera", "Mercado Libre MX"):
                    continue
                self.assertEqual(scraper.discover(), [])
                self.assertEqual(scraper.last_status["state"], "not_configured", scraper.store)

    def _adapter(self, status, payload=None, json_error=False):
        adaptador = JsonEndpointAdapter(endpoint="https://api.example.test/p")
        adaptador.store = "Prueba"
        respuesta = MagicMock(status_code=status)
        if json_error:
            respuesta.json.side_effect = ValueError("no json")
        else:
            respuesta.json.return_value = payload
        adaptador.get = MagicMock(return_value=respuesta)
        return adaptador

    def test_429_and_403_are_reported_with_their_own_state(self):
        for codigo, estado in ((429, "rate_limited"), (403, "forbidden"), (404, "not_found"), (401, "unauthorized")):
            adaptador = self._adapter(codigo)
            self.assertEqual(adaptador.discover(), [])
            self.assertEqual(adaptador.last_status["state"], estado)

    def test_invalid_json_is_invalid_response(self):
        adaptador = self._adapter(200, json_error=True)
        self.assertEqual(adaptador.discover(), [])
        self.assertEqual(adaptador.last_status["state"], "invalid_response")

    def test_valid_payload_returns_products(self):
        payload = {"products": [{"id": "1", "name": "Producto", "price": 100, "listPrice": 1000, "url": "/p/1"}]}
        adaptador = self._adapter(200, payload)
        rows = adaptador.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(adaptador.last_status["state"], "ok")

    def test_one_store_per_target_and_each_isolated(self):
        stores = {s.store for s in api_first_scrapers()}
        for tienda in ("Walmart MX", "Bodega Aurrera", "Chedraui", "Soriana", "Liverpool", "Amazon MX",
                       "Mercado Libre MX", "Coppel", "Suburbia", "Oferstock"):
            self.assertIn(tienda, stores)


if __name__ == "__main__":
    unittest.main()


class HttpRecordTests(unittest.TestCase):
    def test_all_rejected_requests_are_not_reported_as_empty_success(self):
        self.assertEqual(mon.estado_desde_http([403, 403, 429]), "forbidden")
        self.assertEqual(mon.estado_desde_http([503]), "unavailable")
        self.assertEqual(mon.estado_desde_http([200, 403]), None)   # alguna respondió: no es bloqueo total
        self.assertEqual(mon.estado_desde_http([]), None)
        import requests as rq
        self.assertEqual(mon.estado_desde_http([rq.Timeout()]), "timeout")

    def test_source_that_swallows_403_is_reported_forbidden(self):
        import requests as rq

        def scraper_que_traga_errores():
            rq.Session().get("http://127.0.0.1:9/")   # cualquier salida registrada
            return []

        def falso_send(self, request, **kw):
            r = rq.Response()
            r.status_code = 403
            return r

        fuente = mon.Fuente("legacy:demo", set(), scraper_que_traga_errores)
        import requests.sessions as rs
        with patch.object(mon, "definir_fuentes", return_value=[fuente]):
            mon._instalar_registro_http()
            # El envoltorio guarda el 'send' original en su cierre; se sustituye por uno simulado (sin red).
            celda = [c for c in rs.Session.send.__closure__ if callable(c.cell_contents)][0]
            guardado = celda.cell_contents
            celda.cell_contents = falso_send
            try:
                mon.ejecutar_orquestacion_paralela()
            finally:
                celda.cell_contents = guardado
        self.assertEqual(mon.ULTIMO_REPORTE_FUENTES["fuentes"][0]["estado"], "forbidden")
