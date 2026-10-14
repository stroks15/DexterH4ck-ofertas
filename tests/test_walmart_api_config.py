import os
import unittest
from unittest.mock import MagicMock, patch

from scrapers import walmart_api

BASE = {"WALMART_GRAPHQL_URL": "https://gateway.ejemplo.mx/graphql"}


def _resp(status=200, payload=None, json_error=False):
    r = MagicMock()
    r.status_code = status
    if json_error:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = payload if payload is not None else {"data": {}}
    return r


class WalmartApiConfigTests(unittest.TestCase):
    def _llamar(self, entorno, respuesta):
        with (
            patch.dict(os.environ, entorno, clear=True),
            patch("scrapers.walmart_api.time.sleep"),
            patch("scrapers.walmart_api.requests.post", return_value=respuesta) as post,
        ):
            resultado = walmart_api.consultar_liquidaciones_walmart_api("liquidacion")
        return resultado, post

    def test_default_store_id_tenant_and_no_redirects(self):
        _, post = self._llamar(BASE, _resp())
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["variables"]["storeId"], "2033")
        self.assertEqual(kwargs["headers"]["Tenant"], "WM_MEXICO")
        self.assertFalse(kwargs["allow_redirects"])

    def test_empty_store_variable_keeps_default(self):
        _, post = self._llamar({**BASE, "WALMART_STORE_ID": "  "}, _resp())
        self.assertEqual(post.call_args[1]["json"]["variables"]["storeId"], "2033")

    def test_env_overrides_store_and_tenant(self):
        _, post = self._llamar({**BASE, "WALMART_STORE_ID": "0000003362", "WALMART_TENANT": "OTRO"}, _resp())
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["variables"]["storeId"], "0000003362")
        self.assertEqual(kwargs["headers"]["Tenant"], "OTRO")

    def test_user_agent_is_not_a_spoofed_device(self):
        _, post = self._llamar(BASE, _resp())
        ua = post.call_args[1]["headers"]["User-Agent"]
        self.assertNotIn("SM-A065M", ua)
        self.assertNotIn("Android", ua)

    def test_redirect_is_reported_and_not_followed(self):
        resultado, _ = self._llamar(BASE, _resp(302))
        self.assertEqual(resultado, {})
        self.assertEqual(walmart_api.LAST_STATUS["state"], "redirect")

    def test_non_json_body_is_invalid_response(self):
        resultado, _ = self._llamar(BASE, _resp(200, json_error=True))
        self.assertEqual(resultado, {})
        self.assertEqual(walmart_api.LAST_STATUS["state"], "invalid_response")

    def test_missing_endpoint_is_not_configured_without_request(self):
        with patch.dict(os.environ, {}, clear=True), patch("scrapers.walmart_api.requests.post") as post:
            self.assertEqual(walmart_api.consultar_liquidaciones_walmart_api("x"), {})
            post.assert_not_called()
        self.assertEqual(walmart_api.LAST_STATUS["state"], "not_configured")


if __name__ == "__main__":
    unittest.main()
