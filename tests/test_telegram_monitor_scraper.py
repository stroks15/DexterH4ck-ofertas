import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import monitor_ofertas as mon
import scrapers.telegram_monitor_scraper as tg

WALMART = "https://www.walmart.com.mx/ip/pantalla/123456789"
MENSAJE = f"🔥 Pantalla Samsung 55 pulgadas 4K\n❌ Antes $12,999\n✅ Ahora $649\n🔗 {WALMART}"


class ExtraccionTests(unittest.TestCase):
    def test_extrae_url_precios_y_marca_bomba_candidata(self):
        items = tg.candidatos_de_mensaje(MENSAJE, [], "Canal", prioridad=10)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it["tienda"], it["url"]), ("Walmart MX", WALMART))
        self.assertEqual((it["precio_actual"], it["precio_anterior"], it["descuento"]), (649.0, 12999.0, 95))
        self.assertTrue(it["bomba_candidata"])
        self.assertEqual(it["prioridad_fuente"], 10)

    def test_gramaje_no_se_usa_como_precio_de_referencia(self):
        texto = "Arroz Verde Valle 875g\nAntes: $875\nAhora: $55\nhttps://www.walmart.com.mx/ip/arroz/987654321"
        items = tg.candidatos_de_mensaje(texto, [], "Canal")
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["precio_anterior"])
        self.assertEqual(items[0]["descuento"], 0)
        self.assertFalse(items[0]["bomba_candidata"])

    def test_descuento_fuera_de_rango_se_descarta(self):
        texto = f"Producto de prueba muy largo\nAntes $1,000\nAhora $700\n{WALMART}"
        self.assertEqual(tg.candidatos_de_mensaje(texto, [], "Canal"), [])

    def test_urls_con_null_undefined_o_tiendas_ajenas_se_ignoran(self):
        base = "Producto de prueba muy largo\nAntes $1,000\nAhora $100\n"
        for url in ("https://www.walmart.com.mx/ip/null/undefined", "https://www.aliexpress.com/item/1.html"):
            self.assertEqual(tg.candidatos_de_mensaje(base + url, [], "Canal"), [])

    def test_enlace_oculto_y_acortador(self):
        resolver = lambda u: "https://www.amazon.com.mx/dp/B0TEST1234" if "amzn.to" in u else u  # noqa: E731
        texto = "Audífonos Sony inalámbricos\nAntes $2,000 Ahora $300"
        items = tg.candidatos_de_mensaje(texto, ["https://amzn.to/abc123"], "Canal", resolver=resolver)
        self.assertEqual([(i["tienda"], i["descuento"]) for i in items], [("Amazon MX", 85)])

    def test_mensaje_con_varios_productos(self):
        texto = (
            "Pantalla TV Samsung 55 pulgadas\nAntes $10,000 Ahora $4,000\nhttps://www.walmart.com.mx/ip/pantalla/111111111\n"
            "Licuadora Oster 600 watts\nAntes $2,000 Ahora $800\nhttps://www.bodegaaurrera.com.mx/ip/licuadora/222222222"
        )
        items = tg.candidatos_de_mensaje(texto, [], "Canal")
        self.assertEqual([(i["tienda"], i["precio_actual"]) for i in items],
                         [("Walmart MX", 4000.0), ("Bodega Aurrera", 800.0)])


class VistaPublicaTests(unittest.TestCase):
    HTML = """
    <div class="tgme_widget_message"><a class="tgme_widget_message_date" href="https://t.me/Canal/101">
    <time datetime="2026-10-17T10:00:00+00:00">10:00</time></a>
    <div class="tgme_widget_message_text">Aceite Nutrioli 1 litro<br/>Antes <s>$1,200</s> Ahora <b>$55</b><br/>
    <a href="https://www.walmart.com.mx/ip/aceite/55555555">Ver oferta</a></div></div>
    <div class="tgme_widget_message"><time datetime="2026-10-10T10:00:00+00:00">viejo</time>
    <div class="tgme_widget_message_text">Aceite viejo 1 litro Antes $1,200 Ahora $55 https://www.walmart.com.mx/ip/aceite/99999999</div></div>
    """

    def test_parsea_html_con_enlace_oculto_tachado_y_filtra_antiguos(self):
        ahora = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        items, visibles = tg.parsear_vista_publica(self.HTML, "Canal", 5, ahora=ahora)
        self.assertEqual(visibles, 2)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.walmart.com.mx/ip/aceite/55555555")
        self.assertEqual((items[0]["precio_actual"], items[0]["descuento"]), (55.0, 95))
        self.assertEqual(items[0]["origen_url"], "https://t.me/Canal/101")


class FuentesTests(unittest.TestCase):
    def test_solo_fuentes_activas_y_sin_invitacion_en_el_repo(self):
        data = {"fuentes": [
            {"nombre": "A", "tipo": "telegram_canal", "url": "https://t.me/CanalActivo", "autolectura": True, "estado": "activo", "prioridad": 5},
            {"nombre": "B", "tipo": "telegram_canal", "url": "https://t.me/CanalPendiente", "autolectura": False, "estado": "pendiente_integracion"},
            {"nombre": "W", "tipo": "telegram_privado", "lectura": "telethon", "invite_env": "TG_TEST_INVITE", "autolectura": True, "estado": "activo", "prioridad": 10},
            {"nombre": "Web", "tipo": "web", "url": "https://x.com", "autolectura": True, "estado": "activo"},
        ]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(data, fh)
        try:
            with patch.dict(os.environ, {"TG_TEST_INVITE": "https://t.me/+AbCdEfGhIjKlMnOp"}):
                fuentes = tg.cargar_fuentes(fh.name)
        finally:
            os.unlink(fh.name)
        self.assertEqual([(f["nombre"], f["modo"]) for f in fuentes], [("A", "web"), ("W", "telethon")])
        self.assertEqual(fuentes[0]["handle"], "CanalActivo")
        self.assertEqual(fuentes[1]["invite"], "AbCdEfGhIjKlMnOp")

    def test_json_del_repo_activa_los_cuatro_canales(self):
        fuentes = tg.cargar_fuentes("fuentes_liquidaciones.json")
        web = {f["handle"] for f in fuentes if f["modo"] == "web"}
        self.assertTrue({"LiquidaZonaOficial", "ofertasperronasmx", "OfertonesMexico"} <= web)
        self.assertEqual(sum(f["modo"] == "telethon" for f in fuentes), 1)
        with open("fuentes_liquidaciones.json", encoding="utf-8") as fh:
            self.assertNotIn("t.me/+", fh.read())  # el repo es público: sin invitaciones privadas

    def test_sin_json_se_usan_los_valores_por_defecto(self):
        self.assertEqual(len(tg.cargar_fuentes("no_existe.json")), 4)


class PrivadoTests(unittest.TestCase):
    PRIVADO = [{"nombre": "Grupo Walmart Channel", "modo": "telethon", "prioridad": 10, "invite": None, "chat_id": None, "titulo_hint": "walmart"}]
    CREDS = {"TELEGRAM_API_ID": "123456", "TELEGRAM_API_HASH": "hash", "TELEGRAM_SESSION_STRING": "sesion"}

    def test_sin_credenciales_se_omite_sin_error(self):
        limpio = {k: "" for k in self.CREDS}
        with patch.dict(os.environ, limpio), patch.object(tg, "cargar_fuentes", return_value=self.PRIVADO):
            self.assertEqual(tg.buscar_telegram_privado(), [])
        self.assertEqual(tg.STATUS_PRIVADO["state"], "not_configured")

    def test_lee_mensajes_y_propaga_prioridad(self):
        async def falso(*a, **k):
            return [{"id": 1, "fecha": None, "texto": MENSAJE, "enlaces": [], "tachados": []}], "ok"

        with patch.dict(os.environ, self.CREDS), patch.object(tg, "cargar_fuentes", return_value=self.PRIVADO), \
                patch.object(tg, "_mensajes_privados", falso):
            items = tg.buscar_telegram_privado()
        self.assertEqual([i["prioridad_fuente"] for i in items], [10])
        self.assertEqual(tg.STATUS_PRIVADO["state"], "ok")

    def test_timeout_de_sesion_no_propaga(self):
        async def lento(*a, **k):
            await asyncio.sleep(2)

        with patch.dict(os.environ, self.CREDS), patch.object(tg, "cargar_fuentes", return_value=self.PRIVADO), \
                patch.object(tg, "_mensajes_privados", lento), patch.object(tg, "PRIVADO_TIMEOUT", 0.05):
            self.assertEqual(tg.buscar_telegram_privado(), [])
        self.assertEqual(tg.STATUS_PRIVADO["state"], "timeout")

    def test_error_de_telethon_no_propaga(self):
        async def roto(*a, **k):
            raise RuntimeError("boom")

        with patch.dict(os.environ, self.CREDS), patch.object(tg, "cargar_fuentes", return_value=self.PRIVADO), \
                patch.object(tg, "_mensajes_privados", roto):
            self.assertEqual(tg.buscar_telegram_privado(), [])
        self.assertEqual(tg.STATUS_PRIVADO["state"], "error")


class IntegracionMonitorTests(unittest.TestCase):
    def setUp(self):
        p = patch("core.extreme_liquidation.verificar_precio_producto", return_value=(False, "mock"))
        p.start()
        self.addCleanup(p.stop)

    def test_workers_aislados_registrados_en_el_orquestador(self):
        nombres = [f.nombre for f in mon.definir_fuentes()]
        self.assertIn("comunidad:telegram_publico", nombres)
        self.assertIn("comunidad:telegram_privado", nombres)

    def test_bomba_nueva_lleva_bomba_al_inicio_y_no_se_reenvia(self):
        item = tg.candidatos_de_mensaje(MENSAJE, [], "Grupo Walmart Channel", prioridad=10)[0]
        with patch.object(mon.supabase_history, "ya_enviada", return_value=False):
            alertas = mon.procesar_candidatos([dict(item)], {})
            self.assertEqual(len(alertas), 1)
            self.assertTrue(alertas[0]["bomba"])
            self.assertTrue(mon.formatear_alerta(alertas[0]).startswith("💣"))
            historial = {alertas[0]["clave"]: {"precio_alertado": 649.0}}
            self.assertEqual(mon.procesar_candidatos([dict(item)], historial), [])  # historial local
        with patch.object(mon.supabase_history, "ya_enviada", return_value=True):
            self.assertEqual(mon.procesar_candidatos([dict(item)], {}), [])  # Supabase

    def test_fuente_prioritaria_va_antes_a_igual_tipo_de_alerta(self):
        def it(titulo, ident, actual, anterior, prioridad):
            return {"tienda": "Walmart MX", "titulo": titulo, "url": f"https://www.walmart.com.mx/ip/x/{ident}",
                    "precio_actual": actual, "precio_anterior": anterior, "origen_link": "Telegram",
                    "prioridad_fuente": prioridad}

        alertas = mon.procesar_candidatos([it("Aspiradora Dyson ciclónica", "111111111", 3000, 10000, 0),
                                           it("Cafetera Oster programable", "222222222", 800, 2000, 10)], {})
        self.assertEqual([a["item"]["prioridad_fuente"] for a in alertas], [10, 0])


if __name__ == "__main__":
    unittest.main()
