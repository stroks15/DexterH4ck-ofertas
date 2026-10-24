import os
import unittest
from unittest.mock import patch

import preflight


class PreflightAppsScriptProxyTests(unittest.TestCase):
    def test_direct_block_does_not_block_store_when_proxy_is_configured(self):
        targets = {"Walmart MX": ["https://www.walmart.com.mx/"]}
        blocked_probe = {"url": targets["Walmart MX"][0], "status": 403, "state": "forbidden"}
        with patch.dict(os.environ, {"APPS_SCRIPT_PROXY_URL": "https://script.google.com/macros/s/test/exec"}, clear=False), patch.object(preflight, "probe_url", return_value=blocked_probe):
            result = preflight.ejecutar_preflight(targets)
        self.assertEqual(result["stores"]["Walmart MX"]["state"], "ok_via_proxy")
        self.assertFalse(result["stores"]["Walmart MX"]["blocked"])
        self.assertEqual(result["blocked_stores"], [])

    def test_direct_block_remains_blocked_without_proxy(self):
        targets = {"Walmart MX": ["https://www.walmart.com.mx/"]}
        blocked_probe = {"url": targets["Walmart MX"][0], "status": 403, "state": "forbidden"}
        with patch.dict(os.environ, {"APPS_SCRIPT_PROXY_URL": ""}, clear=False), patch.object(preflight, "probe_url", return_value=blocked_probe):
            result = preflight.ejecutar_preflight(targets)
        self.assertEqual(result["blocked_stores"], ["Walmart MX"])


if __name__ == "__main__":
    unittest.main()
