import json
import unittest
from unittest.mock import patch

import monitor_ofertas as mon
from core.discount_text import descuento_declarado
from scrapers import telegram_grupo_privado as grupo
from scrapers import telegram_monitor_scraper as tg


class DescuentoDeclaradoTests(unittest.TestCase):
    def test_formatos_explicitos(self):
        casos = {
            "Walmart Oferstock NACIONAL\n-36%\n\nLabial": 36,
            "TV 55 pulgadas\n70% OFF": 70,
            "Bocina Sony 70% de descuento hoy": 70,
            "Descuento: 85% en licuadoras": 85,
            "Oferta - 60 % en pantallas": 60,
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(descuento_declarado(texto), esperado)

    def test_ignora_lo_que_no_es_descuento_del_producto(self):
        for texto in (
            "Hasta 70% de descuento en toda la tienda",
            "Cashback 60% off en tu compra",
            "Cupón 50% de descuento extra",
            "Bonificación del 80% OFF en monedero",
            "Sin descuento claro",
            "Procesador 12 nucleos 4.5GHz",
            "-150% error",
            "",
            None,
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(descuento_declarado(texto))

    def test_un_gramaje_o_un_precio_no_es_descuento(self):
        self.assertIsNone(descuento_declarado("Cereal 875g Precio: $99.00"))
        self.assertIsNone(descuento_declarado("Leche 1L $22.50 el 50 % es grasa"))

    def test_toma_el_primero_del_texto(self):
        self.assertEqual(descuento_declarado("-55% hoy\nantes 70% OFF"), 55)


MSG = "🔥 {titulo}\n💰 Precio: ${precio}\n📉 {desc}\n👉 {url}"


class TelegramSinPrecioAnteriorTests(unittest.TestCase):
    def _items(self, desc, url, precio="999", titulo="Smart TV Samsung 55 pulgadas Crystal UHD"):
        return tg.candidatos_de_mensaje(MSG.format(titulo=titulo, precio=precio, desc=desc, url=url), [], "Canal X")

    def test_cualquier_tienda_con_50_a_99_declarado_pasa_sin_precio_anterior(self):
        tiendas = {
            "Amazon MX": "https://www.amazon.com.mx/dp/B0ABCDEFGH",
            "Mercado Libre MX": "https://articulo.mercadolibre.com.mx/MLM-1234567890-smart-tv-_JM",
            "Walmart MX": "https://www.walmart.com.mx/ip/pantalla/00123456789",
        }
        for tienda, url in tiendas.items():
            with self.subTest(tienda=tienda):
                items = self._items("-70%", url)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["tienda"], tienda)
                self.assertIsNone(items[0]["precio_anterior"])
                self.assertEqual(items[0]["descuento"], 70)
                self.assertEqual(items[0]["descuento_declarado"], 70)

    def test_menos_de_50_o_mas_de_99_se_descarta(self):
        url = "https://www.amazon.com.mx/dp/B0ABCDEFGH"
        self.assertEqual(self._items("-30%", url), [])
        self.assertEqual(self._items("-49% OFF", url), [])
        self.assertEqual(self._items("100% OFF", url), [])

    def test_limites_50_y_99_incluidos(self):
        url = "https://www.amazon.com.mx/dp/B0ABCDEFGH"
        self.assertEqual(len(self._items("-50%", url)), 1)
        self.assertEqual(len(self._items("-99%", url)), 1)

    def test_con_precio_anterior_manda_el_calculo_real(self):
        texto = "🔥 Smart TV Samsung 55 pulgadas Crystal UHD\nAntes: $5,000\nAhora: $1,000 -20%\nhttps://www.amazon.com.mx/dp/B0ABCDEFGH"
        items = tg.candidatos_de_mensaje(texto, [], "Canal X")
        self.assertEqual(items[0]["descuento"], 80)
        self.assertIsNone(items[0]["descuento_declarado"])

    def test_sin_ningun_descuento_legible_se_deja_al_historial(self):
        items = self._items("", "https://www.amazon.com.mx/dp/B0ABCDEFGH")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["descuento"], 0)
        self.assertIsNone(items[0]["descuento_declarado"])


class MonitorReglaTelegramTests(unittest.TestCase):
    def setUp(self):
        for objetivo in ("core.extreme_liquidation.verificar_precio_producto",):
            p = patch(objetivo, return_value=(False, "mock"))
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(mon.supabase_history, "ya_enviada", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def _candidato(self, desc, url="https://www.amazon.com.mx/dp/B0ABCDEFGH", ident="B0ABCDEFGH"):
        items = tg.candidatos_de_mensaje(
            MSG.format(titulo="Smart TV Samsung 55 pulgadas Crystal UHD", precio="999", desc=desc, url=url),
            [],
            "Canal X",
        )
        for item in items:
            item["id"] = ident
        return items

    def test_70_declarado_se_publica_sin_precio_anterior_ni_inventar_antes(self):
        alertas = mon.procesar_candidatos(self._candidato("-70%"), {})
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0]["descuento"], 70)
        texto = mon.formatear_alerta(alertas[0])
        self.assertNotIn("antes", texto)
        self.assertIn("70% de descuento (indicado en el mensaje de Telegram)", texto)
        self.assertNotIn("💣", texto)

    def test_95_a_99_declarado_lleva_bomba_y_se_envia_una_sola_vez(self):
        historial = {}
        alertas = mon.procesar_candidatos(self._candidato("-96%"), historial)
        self.assertEqual(len(alertas), 1)
        self.assertTrue(mon.formatear_alerta(alertas[0]).startswith("💣"))
        historial[alertas[0]["clave"]]["precio_alertado"] = alertas[0]["actual"]
        self.assertEqual(mon.procesar_candidatos(self._candidato("-96%"), historial), [])

    def test_declarado_bajo_50_no_sale(self):
        self.assertEqual(mon.procesar_candidatos(self._candidato("-40%"), {}), [])

    def test_solo_aplica_a_origen_telegram(self):
        item = {
            "tienda": "Amazon MX",
            "titulo": "Smart TV Samsung 55 pulgadas",
            "url": "https://www.amazon.com.mx/dp/B0ABCDEFGH",
            "precio_actual": 999,
            "precio_anterior": None,
            "descuento_declarado": 70,
            "origen_link": "api",
        }
        self.assertEqual(mon.descuento_declarado_telegram(item), 0)
        self.assertEqual(mon.procesar_candidatos([item], {}), [])
        item["origen_link"] = "Telegram"
        self.assertEqual(mon.descuento_declarado_telegram(item), 70)

    def test_enlace_roto_sigue_rechazado(self):
        candidatos = self._candidato("-96%")
        candidatos[0]["url"] = "https://www.amazon.com.mx/dp/null"
        with patch("core.ai_reparador.reparar_url", side_effect=lambda u, *a, **k: u):
            self.assertEqual(mon.procesar_candidatos(candidatos, {}), [])


class GrupoPrivadoFormatosTests(unittest.TestCase):
    def test_entiende_formato_clasico_y_otros_formatos(self):
        clasico = "Walmart Oferstock NACIONAL\n-36%\n\nLabial\n\nPrecio: $190.00\n(Antes: $299.00)\nSKU: 00007124967483"
        otro = "Amazon MX\nBocina Sony XB100\nPrecio: $300\n70% OFF\nSKU: B0ABC"
        self.assertEqual(grupo.parsear_oferta(clasico)["descuento"], 36)
        parsed = grupo.parsear_oferta(otro)
        self.assertEqual(parsed["descuento"], 70)
        self.assertEqual(parsed["sku"], "B0ABC")
        self.assertIsNone(grupo.parsear_oferta("Sin descuento aquí"))


class RepoPublicoTests(unittest.TestCase):
    def test_json_publico_sin_invitaciones_privadas(self):
        with open("fuentes_liquidaciones.json", encoding="utf-8") as fh:
            crudo = fh.read()
        self.assertNotIn("t.me/+", crudo)
        self.assertNotIn("joinchat", crudo)
        json.loads(crudo)  # sigue siendo JSON válido


if __name__ == "__main__":
    unittest.main()
