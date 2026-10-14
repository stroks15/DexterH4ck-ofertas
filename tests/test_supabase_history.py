import os
import unittest
from unittest.mock import MagicMock, patch

import requests

from core import supabase_history as sh

ENTORNO = {"SUPABASE_URL": "https://proyecto.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "clave-de-prueba"}


def _resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


class SupabaseDisabledTests(unittest.TestCase):
    def test_without_env_everything_is_noop(self):
        with patch.dict(os.environ, {}, clear=True), patch("core.supabase_history.requests") as http:
            self.assertFalse(sh.habilitado())
            self.assertIsNone(sh.ya_enviada("walmart:1"))
            self.assertFalse(sh.registrar_envio("walmart:1", {}, 96))
            http.get.assert_not_called()
            http.post.assert_not_called()

    def test_non_https_url_is_rejected(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "http://inseguro.local", "SUPABASE_KEY": "k"}, clear=True):
            self.assertFalse(sh.habilitado())


class SupabaseQueryTests(unittest.TestCase):
    def test_ya_enviada_true_false_and_unknown(self):
        with patch.dict(os.environ, ENTORNO, clear=True), patch("core.supabase_history.requests.get") as get:
            get.return_value = _resp(200, [{"clave": "walmart:1"}])
            self.assertIs(sh.ya_enviada("walmart:1"), True)
            get.return_value = _resp(200, [])
            self.assertIs(sh.ya_enviada("walmart:2"), False)
            get.return_value = _resp(500)
            self.assertIsNone(sh.ya_enviada("walmart:3"))  # desconocido, no "False"

    def test_network_error_is_unknown_not_exception(self):
        with (
            patch.dict(os.environ, ENTORNO, clear=True),
            patch("core.supabase_history.requests.get", side_effect=requests.ConnectionError("x")),
        ):
            self.assertIsNone(sh.ya_enviada("walmart:1"))

    def test_query_uses_service_key_headers_and_no_redirects(self):
        with patch.dict(os.environ, ENTORNO, clear=True), patch("core.supabase_history.requests.get") as get:
            get.return_value = _resp(200, [])
            sh.ya_enviada("walmart:1")
            _, kwargs = get.call_args
            self.assertEqual(kwargs["headers"]["apikey"], "clave-de-prueba")
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["params"]["clave"], "eq.walmart:1")

    def test_registrar_envio_posts_row_ignoring_duplicates(self):
        item = {
            "tienda": "Walmart MX",
            "titulo": "TV 55",
            "precio_actual": 50,
            "precio_anterior": 1000,
            "url": "https://www.walmart.com.mx/ip/tv/123",
        }
        with patch.dict(os.environ, ENTORNO, clear=True), patch("core.supabase_history.requests.post") as post:
            post.return_value = _resp(201)
            self.assertTrue(sh.registrar_envio("walmart:123", item, 95))
            _, kwargs = post.call_args
            self.assertEqual(kwargs["json"]["clave"], "walmart:123")
            self.assertEqual(kwargs["json"]["descuento"], 95)
            self.assertIn("ignore-duplicates", kwargs["headers"]["Prefer"])

    def test_secrets_never_logged(self):
        with (
            patch.dict(os.environ, ENTORNO, clear=True),
            patch("core.supabase_history.requests.get", return_value=_resp(500)),
            self.assertLogs("DexterH4ck.Supabase", level="WARNING") as logs,
        ):
            sh.ya_enviada("walmart:1")
        salida = "\n".join(logs.output)
        self.assertNotIn("clave-de-prueba", salida)
        self.assertNotIn("proyecto.supabase.co", salida)


if __name__ == "__main__":
    unittest.main()
