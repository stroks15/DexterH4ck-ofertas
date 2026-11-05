import unittest
from unittest.mock import MagicMock, patch

from scrapers.firecrawl_enrichment import scrape_markdown


class FirecrawlIntegrationTests(unittest.TestCase):
    @patch.dict("os.environ", {"FIRECRAWL_API_KEY": "test-key", "FIRECRAWL_API_URL": "https://api.firecrawl.dev/v2"})
    @patch("scrapers.firecrawl_enrichment.requests.post")
    def test_scrape_uses_v2_markdown_and_bearer_auth(self, post):
        response = MagicMock()
        response.json.return_value = {"success": True, "data": {"markdown": "# Producto", "metadata": {"title": "Ficha"}}}
        post.return_value = response
        result = scrape_markdown("https://tienda.example/producto/1")
        self.assertEqual(result["markdown"], "# Producto")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.firecrawl.dev/v2/scrape")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(kwargs["json"]["formats"], ["markdown"])

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_key_is_noop(self):
        self.assertIsNone(scrape_markdown("https://tienda.example/producto/1"))


if __name__ == "__main__":
    unittest.main()
