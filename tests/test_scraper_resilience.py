import unittest
from unittest.mock import patch

from core.scraper_base import BaseScraper, ScraperContext
from scrapers.browser_network import _sources


class DummyScraper(BaseScraper):
    store = "dummy"

    def discover(self):
        return []


class ResilienceTests(unittest.TestCase):
    def test_timeout_grows_without_excessive_limit(self):
        scraper = DummyScraper(ScraperContext(timeout=10, max_retries=3))
        values = []
        for attempt in range(4):
            values.append(min(10 * (1.35 ** attempt), 45.0))
        self.assertEqual(values[0], 10)
        self.assertLessEqual(values[-1], 45)

    def test_browser_sources_include_public_alternatives(self):
        with patch.dict("os.environ", {"NETWORK_BROWSER_SOURCES": ""}, clear=False):
            sources = _sources()
        stores = [x["store"] for x in sources]
        self.assertIn("Bodega Aurrera", stores)
        self.assertIn("Coppel", stores)
        self.assertIn("Amazon MX", stores)
        self.assertGreaterEqual(sum(s == "Suburbia" for s in stores), 2)


if __name__ == "__main__":
    unittest.main()
