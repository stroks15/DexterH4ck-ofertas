# Perímetro

Capa de cobertura de tiendas en línea con transporte stealth. Su trabajo es **llegar** a la ficha y
devolver filas normalizadas; la decisión de publicar sigue siendo del pipeline.

## Principios

1. **Config antes que código.** Una tienda es un registro en `config/perimetro_tiendas.json`.
2. **Estrategias en orden, auto-verificadas.** `["vtex","html"]`: prueba VTEX, si responde catálogo se
   queda con esa vía; si no, cae a HTML. Nada se asume por el dominio.
3. **Estado honesto.** `ok`, `empty`, `not_configured`, `blocked_waf`, `captcha_required`,
   `rate_limited`, `timeout`, `sin_navegador`, `invalid_response`. Nunca un bloqueo disfrazado de éxito.
4. **Un bloqueo cuesta un cooldown, no un ciclo.** `perimetro_cooldown.json` guarda host, motivo y
   expiración; se salta hasta que expire (`--forzar` lo ignora).

## Escalado de transporte

`http -> stealth -> browser`. `http` usa huella TLS de Chrome; `stealth` usa patchright con spoof de
fingerprint; `browser` es el último recurso con scripts de evasión propios.

## Uso

```bash
python -m perimetro doctor
python -m perimetro probe --tienda nike --q tenis
python -m perimetro crawl --tiendas todas --q liquidacion --max 20 --out ofertas.json
python -m perimetro report
