import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scrapers import tiendas_fisicas


class PhysicalStoreCooldownTests(unittest.TestCase):
    def test_http_429_persists_cooldown_and_stops_the_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            cooldown_file = Path(tmp) / "fuentes_cooldown.json"
            response = MagicMock(status_code=429, text="")
            session = MagicMock()
            session.get.return_value = response
            with (
                patch.object(tiendas_fisicas, "GOOGLE_COOLDOWN_FILE", cooldown_file),
                patch.dict("os.environ", {"GOOGLE_SEARCH_COOLDOWN_SECONDS": "3600"}, clear=False),
            ):
                result = tiendas_fisicas.buscar_tiendas_fisicas(session=session)

            self.assertEqual(result, [])
            session.get.assert_called_once()
            state = json.loads(cooldown_file.read_text(encoding="utf-8"))
            self.assertGreater(state["google_search_until"], 0)
            self.assertEqual(state["reason"], "HTTP 429 rate limit")

    def test_active_cooldown_skips_google_without_any_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            cooldown_file = Path(tmp) / "fuentes_cooldown.json"
            cooldown_file.write_text(
                json.dumps({"google_search_until": 4_000_000_000, "reason": "HTTP 429 rate limit"}),
                encoding="utf-8",
            )
            session = MagicMock()
            with patch.object(tiendas_fisicas, "GOOGLE_COOLDOWN_FILE", cooldown_file):
                result = tiendas_fisicas.buscar_tiendas_fisicas(session=session)

            self.assertEqual(result, [])
            session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
