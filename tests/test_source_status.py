import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

import preflight
from core.source_resilience import BLOCKING_STATES, classify_status, probe_url


class ClassifyStatusTests(unittest.TestCase):
    def test_codes_map_to_distinct_states(self):
        esperado = {
            200: "ok", 301: "redirect", 302: "redirect", 400: "bad_request",
            401: "unauthorized", 403: "forbidden", 404: "not_found", 408: "timeout",
            409: "conflict", 429: "rate_limited", 500: "server_error",
            502: "unavailable", 503: "unavailable", 504: "unavailable", None: "network_error",
        }
        for codigo, estado in esperado.items():
            self.assertEqual(classify_status(codigo), estado, codigo)

    def test_rate_limit_forbidden_and_missing_are_not_the_same(self):
        self.assertEqual(len({classify_status(c) for c in (403, 404, 429, 503)}), 4)

    def test_blocking_states_do_not_include_missing_endpoint_or_bad_credential(self):
        self.assertNotIn(classify_status(404), BLOCKING_STATES)
        self.assertNotIn(classify_status(401), BLOCKING_STATES)
        self.assertIn(classify_status(429), BLOCKING_STATES)
        self.assertIn(classify_status(403), BLOCKING_STATES)


class ProbeUrlTests(unittest.TestCase):
    def _session(self, status, text=""):
        response = MagicMock(status_code=status, text=text, url="https://x.test/")
        session = MagicMock()
        session.get.return_value = response
        return session

    def test_captcha_in_200_is_reported_as_blocked_not_ok(self):
        r = probe_url("https://x.test/", session=self._session(200, "<html>Please solve the CAPTCHA</html>"))
        self.assertEqual(r["state"], "blocked")

    def test_http_429_is_rate_limited(self):
        self.assertEqual(probe_url("https://x.test/", session=self._session(429))["state"], "rate_limited")

    def test_timeout_and_network_errors_never_raise(self):
        session = MagicMock()
        session.get.side_effect = requests.Timeout()
        self.assertEqual(probe_url("https://x.test/", session=session)["state"], "timeout")
        session.get.side_effect = requests.ConnectionError()
        self.assertEqual(probe_url("https://x.test/", session=session)["state"], "network_error")


class PreflightTests(unittest.TestCase):
    def test_blocked_store_is_listed_but_does_not_fail_globally(self):
        def fake_probe(url, session=None, timeout=15.0):
            if "walmart" in url:
                return {"url": url, "status": 403, "state": "forbidden"}
            return {"url": url, "status": 200, "state": "ok"}

        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch("preflight.probe_url", side_effect=fake_probe):
                    codigo = preflight.main()
                self.assertEqual(codigo, 0)
                with open("preflight_health.json", encoding="utf-8") as fh:
                    health = json.load(fh)
                self.assertEqual(health["blocked_stores"], ["Walmart MX"])
                self.assertEqual(health["stores"]["Chedraui"]["state"], "ok")
                with open("preflight.env", encoding="utf-8") as fh:
                    env = fh.read()
                self.assertIn("PREFLIGHT_BLOCKED_STORES=Walmart MX", env)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
