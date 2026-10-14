import os
import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from scrapers import discovery_utils as du
from scrapers import tiendas_mexico as tm
from scrapers.api_stores import JsonEndpointAdapter
from scrapers.vtex_stores import VtexStoresScraper


def _payload(n, desde=0):
    return {
        "data": {
            "search": {
                "products": [
                    {
                        "id": str(desde + i),
                        "name": f"Producto {desde + i}",
                        "canonicalUrl": f"/ip/p/{desde + i}",
                        "priceInfo": {"currentPrice": {"price": 10}, "wasPrice": {"price": 100}},
                    }
                    for i in range(n)
                ]
            }
        }
    }


class _Parser:
    def parsear_respuesta_busqueda(self, payload):
        return [{"id": p["id"], "url": p["canonicalUrl"]} for p in payload["data"]["search"]["products"]]


class PaginationTests(unittest.TestCase):
    def test_pages_only_when_previous_is_full_and_dedupes(self):
        estado = {"state": "ok"}
        llamadas = []

        def consultar(termino, pagina=1, tamano=40):
            llamadas.append((termino, pagina))
            return _payload(40) if pagina == 1 else _payload(3)  # misma pág. 1 en ambos términos

        r = du.buscar_graphql_paginado(consultar, _Parser(), ["a", "b"], paginas=3, tamano=40, estado=estado)
        self.assertEqual(llamadas, [("a", 1), ("a", 2), ("b", 1), ("b", 2)])
        self.assertEqual(len({x["id"] for x in r}), len(r))  # sin duplicados entre términos

    def test_block_stops_the_whole_source_but_keeps_what_was_found(self):
        estado = {"state": "ok"}

        def consultar(termino, pagina=1, tamano=40):
            if termino == "b":
                estado["state"] = "rate_limited"
                return {}
            return _payload(2)

        r = du.buscar_graphql_paginado(consultar, _Parser(), ["a", "b", "c"], 2, 40, estado)
        self.assertEqual(len(r), 2)
        self.assertEqual(estado["state"], "rate_limited")  # no se disfraza de éxito

    def test_timeout_skips_term_not_source(self):
        estado = {"state": "ok"}

        def consultar(termino, pagina=1, tamano=40):
            estado["state"] = "timeout" if termino == "a" else "ok"
            return {} if termino == "a" else _payload(1)

        self.assertEqual(len(du.buscar_graphql_paginado(consultar, _Parser(), ["a", "b"], 2, 40, estado)), 1)

    def test_env_helpers_tolerate_empty_and_invalid(self):
        with patch.dict(os.environ, {"X_PAGES": "", "Y_PAGES": "abc", "Z_PAGES": "99", "T": " a, ,b "}):
            self.assertEqual(du.env_int("X_PAGES", 2), 2)
            self.assertEqual(du.env_int("Y_PAGES", 2), 2)
            self.assertEqual(du.env_int("Z_PAGES", 2, 1, 5), 5)
            self.assertEqual(du.env_terminos("T", ("z",)), ("a", "b"))
            self.assertEqual(du.env_terminos("NO_EXISTE", ("z",)), ("z",))


class VtexTests(unittest.TestCase):
    def _producto(self, pid, sellers):
        return {
            "productId": pid,
            "productName": f"Prod {pid}",
            "link": f"/prod-{pid}/p",
            "brand": "M",
            "items": [{"sellers": sellers}],
        }

    def _oferta(self, price, lista, qty=5):
        return {"commertialOffer": {"Price": price, "ListPrice": lista, "AvailableQuantity": qty}}

    def test_picks_best_discount_among_available_sellers(self):
        prod = self._producto(1, [self._oferta(500, 600), self._oferta(100, 1000), self._oferta(1, 1000, qty=0)])
        precio, lista = VtexStoresScraper._offer(prod)
        self.assertEqual((precio, lista), (100.0, 1000.0))

    def test_paginates_with_best_discount_order_and_dedupes(self):
        v = VtexStoresScraper(endpoint="https://x.test/api/search")
        pagina1 = [self._producto(i, [self._oferta(10, 100)]) for i in range(50)]
        pagina2 = [self._producto(i, [self._oferta(10, 100)]) for i in range(50, 55)]
        respuestas = [(("ok"), pagina1), ("ok", pagina2)] + [("ok", [])] * 20
        paramsvistos = []

        def pedir(params):
            paramsvistos.append(dict(params))
            return respuestas[len(paramsvistos) - 1]

        v._pedir_pagina = pedir
        with patch.dict(os.environ, {"CHEDRAUI_QUERIES": "", "CHEDRAUI_VTEX_PAGES": "3"}):
            filas = v.discover()
        self.assertEqual(len(filas), 55)
        self.assertEqual(paramsvistos[0]["_from"], "0")
        self.assertEqual(paramsvistos[1]["_from"], "50")
        self.assertEqual(paramsvistos[0]["O"], "OrderByBestDiscountDESC")
        self.assertEqual(v.last_status["state"], "ok")

    def test_block_without_rows_is_reported(self):
        v = VtexStoresScraper(endpoint="https://x.test/api/search")

        def pedir(params):
            v.last_status = {"state": "forbidden", "http": 403}
            return "forbidden", None

        v._pedir_pagina = pedir
        self.assertEqual(v.discover(), [])
        self.assertEqual(v.last_status["state"], "forbidden")


class GenericAdapterTests(unittest.TestCase):
    def test_placeholders_expand_terms_and_pages(self):
        a = type(
            "S", (JsonEndpointAdapter,), {"store": "Soriana", "endpoint_name": "SORIANA_API", "home": "https://h"}
        )(endpoint="https://api.t/search?q={q}&page={page}")
        with patch.dict(os.environ, {"SORIANA_QUERIES": "remate,outlet", "SORIANA_PAGES": "2"}):
            urls = a._urls()
        self.assertEqual(len(urls), 4)
        self.assertIn("https://api.t/search?q=remate&page=2", urls)

    def test_plain_endpoint_stays_single_url(self):
        a = JsonEndpointAdapter(endpoint="https://api.t/x")
        self.assertEqual(a._urls(), ["https://api.t/x"])


class SorianaHtmlTests(unittest.TestCase):
    HTML = """<div class="product-card" data-pid="77"><a class="product-title-link" href="/p/77">TV</a>
      <span class="strike-through"><span class="value" content="1000.00">$1,000.00</span></span>
      <span class="sales"><span class="value" content="100.00">$100.00</span></span></div>"""

    def test_extracts_strikethrough_reference_price(self):
        filas = tm._parsear_html_por_tienda(self.HTML, "soriana", "https://www.soriana.com/buscar?q=x")
        self.assertEqual(filas[0]["precio_actual"], 100.0)
        self.assertEqual(filas[0]["precio_anterior"], 1000.0)

    def test_no_reference_when_not_present(self):
        html = '<div class="product-card"><a class="product-title-link" href="/p/1">A</a><span class="value">$50</span></div>'
        self.assertIsNone(tm._parsear_html_por_tienda(html, "soriana", "u")[0]["precio_anterior"])


if __name__ == "__main__":
    unittest.main()
