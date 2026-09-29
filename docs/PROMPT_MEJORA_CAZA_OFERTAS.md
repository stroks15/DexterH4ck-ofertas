# Prompt maestro - DexterH4ck Ofertas

Actúa como arquitecto senior Python, Supabase y bots Telegram. Mejora este repositorio sin romper la lógica existente.

Objetivo: convertir DexterH4ck-ofertas en un sistema inteligente de detección de ofertas y liquidaciones en México.

## Base actual
- Mantener scrapers existentes.
- Mantener historial_ofertas.json como protección contra duplicados.
- Mantener envío Telegram actual.
- Integrar una capa de datos preparada para Supabase.

## Nuevas funciones

1. Módulo de liquidaciones:
- Crear tablas para tiendas, productos, ofertas, historial y fuentes.
- Separar liquidación real de descuento normal.
- Detectar palabras: liquidación, remate, outlet, última pieza, saldo, caja abierta, open box.
- Guardar evidencia del precio anterior y precio actual.

2. Motor de puntuación:
- Calcular nivel de oportunidad.
- Variables: porcentaje descuento, disponibilidad, histórico, categoría y tienda.

3. Telegram:
- Mensajes enriquecidos con emoji.
- Producto, tienda, precio anterior, precio actual, descuento, enlace y motivo de alerta.
- Evitar spam mediante hash único.

4. Fuentes:
Preparar arquitectura para incorporar fuentes externas configurables.
No depender de accesos no autorizados.

5. Calidad:
- Variables mediante .env.
- Logs claros.
- Manejo de errores.
- Tests básicos.
- GitHub Actions estable.

Entrega cambios pequeños, documentados y con commits claros.
