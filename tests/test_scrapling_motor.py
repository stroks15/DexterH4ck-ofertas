# tests/test_scrapling_motor.py
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core.scrapling_motor import Config, Motor, Presupuesto, Resultado, Rotador, clasificar, extraer_minimo

RAIZ = Path(__file__).resolve().parent.parent


class PruebasMotor(unittest.TestCase):
    def setUp(self):
        data = json.loads((RAIZ / "config" / "scrapling_motor.json").read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        ruta = Path(self.tmp.name) / "motor.json"
        ruta.write_text(json.dumps(data), encoding="utf-8")
        self.config = Config(ruta)
        self.addCleanup(self.tmp.cleanup)

    def test_perfiles_coherentes(self):
        for perfil in self.config.perfiles:
            self.assertTrue(perfil.ua)
            self.assertGreaterEqual(perfil.ancho, 320)
            cabeceras = perfil.cabeceras()
            self.assertEqual(cabeceras["User-Agent"], perfil.ua)
            self.assertEqual(cabeceras["Sec-Ch-Ua-Mobile"], "?1" if perfil.movil else "?0")

    def test_rotacion_pegajosa(self):
        rotador = Rotador(self.config)
        primera = rotador.siguiente("www.nike.com")
        segunda = rotador.siguiente("www.nike.com")
        self.assertEqual(primera.id, segunda.id)
        self.assertGreater(len(self.config.perfiles_activos()), 1)

    def test_presupuesto_crece_y_topa(self):
        presupuesto = Presupuesto(self.config.presupuesto)
        base = presupuesto.timeout("x.mx")
        for _ in range(20):
            presupuesto.observar("x.mx", 20000)
        self.assertGreater(presupuesto.timeout("x.mx"), base)
        self.assertLessEqual(presupuesto.timeout("x.mx"), presupuesto.maximo)
        self.assertEqual(presupuesto.espera_retry_after("999"), presupuesto.tope_retry)

    def test_requiere_navegador(self):
        self.assertTrue(self.config.requiere_navegador("www.walmart.com.mx"))
        self.assertFalse(self.config.requiere_navegador("www.nike.com"))
        self.assertFalse(self.config.requiere_navegador("api.mercadolibre.com"))

    def test_clasificar(self):
        self.assertEqual(clasificar(200, {}, "Just a moment..."), "blocked_waf")
        self.assertEqual(clasificar(200, {}, "<div class='g-recaptcha'></div>"), "captcha_required")
        self.assertEqual(clasificar(200, {}, "<html>ok</html>"), "ok")
        self.assertEqual(clasificar(429, {}, ""), "rate_limited")

    def test_extraccion_minima(self):
        html = '<div class="product"><a class="name" href="/p/9">Licuadora</a><span class="price">$899.00</span></div>'
        filas = extraer_minimo(html, {"card": "div.product", "title": "a.name", "price": "span.price", "link": "a.name"})
        self.assertTrue(filas)
        self.assertEqual(filas[0]["precio_actual"], 899.0)

    def test_fallback_usa_respaldo(self):
        motor = Motor(self.config)

        async def falso(url, **kw):
            return Resultado(ok=False, estado="blocked_waf", http=403)

        motor.pedir = falso  # type: ignore[assignment]
        resultado = asyncio.run(motor.pedir_o_fallback("https://x.mx/a", lambda: [{"titulo": "a", "url": "b", "precio_actual": 1}]))
        self.assertTrue(resultado.fallback)
        self.assertEqual(resultado.filas[0]["titulo"], "a")

    def test_fallback_no_pisa_motor(self):
        motor = Motor(self.config)

        async def bueno(url, **kw):
            return Resultado(ok=True, estado="ok", http=200, texto="x")

        motor.pedir = bueno  # type: ignore[assignment]
        resultado = asyncio.run(motor.pedir_o_fallback("https://x.mx/a", lambda: [{"titulo": "legacy"}], filas_de=lambda r: [{"titulo": "motor"}]))
        self.assertFalse(resultado.fallback)
        self.assertEqual(resultado.filas[0]["titulo"], "motor")

    def test_cooldown_bloquea_host(self):
        motor = Motor(self.config)
        motor.cooldown.registrar("x.mx", "blocked_waf")
        resultado = asyncio.run(motor.pedir("https://x.mx/a"))
        self.assertEqual(resultado.estado, "cooldown")


if __name__ == "__main__":
    unittest.main()
