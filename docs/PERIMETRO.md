# Perímetro de tiendas

`config/perimetro_tiendas.json` declara las 17 tiendas y únicamente URLs públicas de búsqueda/catálogo. `perimetro` contiene registry, ritmo por host, pool de proxy opcional, clasificación WAF/CAPTCHA, cooldown persistente, transporte y extracción mínima de JSON-LD, `__NEXT_DATA__` y HTML.

Uso:

```bash
python -m perimetro doctor
python -m perimetro probe --tienda elektra --q liquidacion
python -m perimetro report
```

Un HTTP 200 que contiene `Just a moment` se clasifica `blocked_waf`. CAPTCHA queda en `captcha_required`. Los retos no se eluden ni se resuelven automáticamente; una tienda bloqueada entra en cooldown para evitar reintentos agresivos.
