"""Pruebas deterministas (sin red) del respaldo ScraperAPI."""

from __future__ import annotations

import logging
import unittest
from unittest import mock

import requests

from core import network_manager as nm
from core.network_manager import ScraperApiManager
from core.scraper_base import BaseScraper, ScraperContext

TARGET = "https://www.liverpool.com.mx/tienda?s=ofertas"


def fake_response(status: int = 200, body: str = "{}", url: str = TARGET, content_type: str = "application/json"):
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8")
    resp.url = url
    resp.headers["Content-Type"] = content_type
    return resp


def manager(**kwargs) -> ScraperApiManager:
    kwargs.setdefault("api_key", "SECRET123")
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("max_credits", 100)
    return ScraperApiManager(**kwargs)


class ManagerTests(unittest.TestCase):
    def test_sin_clave_queda_apagado_y_no_llama_a_la_api(self):
        mgr = ScraperApiManager(api_key="", enabled=True)
        self.assertFalse(mgr.enabled)
        with mock.patch.object(nm.requests, "get") as api:
            result = mgr.run(lambda: fake_response(403), TARGET)
        self.assertEqual(result.status_code, 403)
        api.assert_not_called()

    def test_403_directo_se_recupera_via_scraperapi_sin_exponer_la_clave(self):
        mgr = manager()
        proxied = fake_response(200, '{"ok": 1}', "https://api.scraperapi.com/?api_key=SECRET123")
        with mock.patch.object(nm.requests, "get", return_value=proxied) as api:
            result = mgr.run(lambda: fake_response(403), TARGET)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.url, TARGET)
        self.assertNotIn("SECRET123", result.url)
        self.assertEqual(result.headers["X-DexterH4ck-Via"], "scraperapi")
        params = api.call_args.kwargs["params"]
        self.assertEqual(params["url"], TARGET)
        self.assertEqual(params["country_code"], "mx")
        self.assertNotIn("render", params)

    def test_estados_que_no_activan_respaldo(self):
        mgr = manager()
        with mock.patch.object(nm.requests, "get") as api:
            for status in (200, 404, 429, 500):
                mgr.run(lambda s=status: fake_response(s), TARGET)
        api.assert_not_called()

    def test_timeout_y_connection_error_activan_respaldo(self):
        for exc in (requests.ReadTimeout("t"), requests.ConnectionError("c")):
            mgr = manager()

            def primary(e=exc):
                raise e

            with mock.patch.object(nm.requests, "get", return_value=fake_response(200)):
                self.assertEqual(mgr.run(primary, TARGET).status_code, 200)

    def test_si_el_respaldo_falla_se_relanza_la_excepcion_original(self):
        mgr = manager()

        def primary():
            raise requests.ReadTimeout("t")

        with mock.patch.object(nm.requests, "get", side_effect=requests.ConnectionError("SECRET123")):
            with self.assertRaises(requests.ReadTimeout):
                mgr.run(primary, TARGET)

    def test_cuota_agotada_desactiva_y_deja_continuar(self):
        mgr = manager()
        quota = fake_response(403, "You have exhausted the API Credits available in this monthly cycle.")
        with self.assertLogs("DexterH4ck.ScraperApi", level=logging.WARNING) as logs:
            with mock.patch.object(nm.requests, "get", return_value=quota) as api:
                first = mgr.run(lambda: fake_response(403), TARGET)
                second = mgr.run(lambda: fake_response(503), TARGET + "&p=2")
        self.assertEqual(first.status_code, 403)  # se devuelve el resultado original
        self.assertEqual(second.status_code, 503)
        self.assertEqual(api.call_count, 1)  # tras desactivarse no vuelve a llamar
        self.assertFalse(mgr.enabled)
        self.assertIn("quota exhausted or expired", " ".join(logs.output))

    def test_clave_invalida_desactiva(self):
        mgr = manager()
        with mock.patch.object(nm.requests, "get", return_value=fake_response(401, "Unauthorized")):
            mgr.run(lambda: fake_response(403), TARGET)
        self.assertFalse(mgr.enabled)

    def test_403_sin_pista_de_cuota_no_desactiva_pero_corta_el_host(self):
        mgr = manager()
        with mock.patch.object(nm.requests, "get", return_value=fake_response(403, "blocked")) as api:
            for _ in range(4):
                mgr.run(lambda: fake_response(403), TARGET)
        self.assertTrue(mgr.enabled)
        self.assertEqual(api.call_count, 2)  # circuito por host

    def test_nunca_envia_hosts_propios_ni_credenciales(self):
        mgr = manager()
        casos = [
            ("https://script.google.com/macros/s/ABC/exec", None),
            ("https://script.googleusercontent.com/macros/echo?x=1", None),
            ("https://api.telegram.org/bot1/sendMessage", None),
            ("https://tienda.com/api?token=abc", None),
            ("https://tienda.com/api?apikey=abc", None),
            ("https://user:pass@tienda.com/api", None),
            (TARGET, {"Authorization": "Bearer x"}),
            (TARGET, {"Cookie": "a=b"}),
        ]
        with mock.patch.object(nm.requests, "get") as api:
            for url, headers in casos:
                self.assertIsNone(mgr.fetch(url, headers=headers), url)
        api.assert_not_called()

    def test_presupuesto_por_ejecucion(self):
        mgr = manager(max_credits=2)
        with mock.patch.object(nm.requests, "get", return_value=fake_response(200)) as api:
            for i in range(4):
                mgr.run(lambda: fake_response(403), f"{TARGET}&i={i}")
        self.assertEqual(api.call_count, 2)

    def test_render_es_un_escalon_extra_solo_si_se_pide(self):
        mgr = manager()
        respuestas = [fake_response(500), fake_response(200, "<html>ok</html>", content_type="text/html")]
        with mock.patch.object(nm.requests, "get", side_effect=respuestas) as api:
            result = mgr.run(lambda: fake_response(403), TARGET, render=True)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["X-DexterH4ck-Via"], "scraperapi-render")
        self.assertEqual(api.call_args_list[1].kwargs["params"]["render"], "true")

        mgr2 = manager()
        with mock.patch.object(nm.requests, "get", return_value=fake_response(500)) as api2:
            result2 = mgr2.run(lambda: fake_response(403), TARGET)  # sin render
        self.assertEqual(result2.status_code, 403)
        self.assertEqual(api2.call_count, 1)

    def test_enabled_false_por_entorno(self):
        with mock.patch.dict("os.environ", {"SCRAPERAPI_KEY": "k", "SCRAPERAPI_ENABLED": "false"}):
            self.assertFalse(ScraperApiManager().enabled)
        with mock.patch.dict("os.environ", {"SCRAPERAPI_KEY": "k"}, clear=True):
            self.assertTrue(ScraperApiManager().enabled)


class _Dummy(BaseScraper):
    store = "Dummy"

    def discover(self):
        return []


class BaseScraperIntegrationTests(unittest.TestCase):
    def setUp(self):
        nm.reset_scraperapi_manager()
        self.scraper = _Dummy(ScraperContext(max_retries=0))

    def tearDown(self):
        nm.reset_scraperapi_manager()

    def test_get_usa_respaldo_en_403(self):
        mgr = manager()
        with mock.patch("core.scraper_base.get_scraperapi_manager", return_value=mgr):
            with mock.patch.object(self.scraper.session, "get", return_value=fake_response(403)):
                with mock.patch.object(nm.requests, "get", return_value=fake_response(200, '{"a":1}')):
                    self.assertEqual(self.scraper.get(TARGET).status_code, 200)

    def test_get_sin_clave_se_comporta_como_antes(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            nm.reset_scraperapi_manager()
            with mock.patch.object(self.scraper.session, "get", return_value=fake_response(403)):
                with mock.patch.object(nm.requests, "get") as api:
                    self.assertEqual(self.scraper.get(TARGET).status_code, 403)
        api.assert_not_called()

    def test_allow_fallback_false_y_auth_no_usan_proxy(self):
        mgr = manager()
        with mock.patch("core.scraper_base.get_scraperapi_manager", return_value=mgr):
            with mock.patch.object(self.scraper.session, "get", return_value=fake_response(403)):
                with mock.patch.object(nm.requests, "get") as api:
                    self.scraper.get(TARGET, allow_fallback=False)
                    self.scraper.get(TARGET, auth=("u", "p"))
        api.assert_not_called()

    def test_params_con_token_no_se_envian(self):
        mgr = manager()
        with mock.patch("core.scraper_base.get_scraperapi_manager", return_value=mgr):
            with mock.patch.object(self.scraper.session, "get", return_value=fake_response(403)):
                with mock.patch.object(nm.requests, "get") as api:
                    self.scraper.get("https://tienda.com/api", params={"token": "abc"})
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
