# Motor Scrapling opcional

`core.scrapling_motor` y `scrapers.scrapling_adaptador` son una capa primaria por tienda. Se activa explícitamente con `SCRAPLING_MOTOR_ENABLED=true` y `requirements-scrapling.txt` instalado. Si Scrapling, Perímetro o una petición no producen filas, se ejecuta el adaptador API existente como respaldo y se registra `fallback: true`.

El motor usa concurrencia 4 y presupuesto adaptativo. Trata WAF, CAPTCHA, rate limit, timeout y respuestas inválidas como estados distintos de `ok`; no resuelve desafíos, suplantaciones ni CAPTCHA. `Mercado Libre MX` permanece en su adaptador de API actual.
