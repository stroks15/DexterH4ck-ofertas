# Liquidaciones reales y precios extremos

## Qué significa 🟢 y 🔴

### 🟢 VERDE — oferta/liquidación con evidencia comparable
Se usa cuando el bot tiene:
- URL directa de producto o fuente oficial de liquidaciones.
- Precio actual.
- Precio anterior/referencia explícito o una referencia histórica guardada.
- Descuento calculable de forma reproducible.

Verde no significa compra garantizada. El stock, la sucursal, el cupón, la talla/color y las condiciones de la tienda pueden cambiar.

### 🔴 ROJO — pista o liquidación especial no comparable
Se usa cuando hay una señal fuerte pero no existe una referencia de precio suficiente para calcular el descuento, por ejemplo:
- precio extremadamente bajo;
- remate/outlet sin precio anterior;
- una publicación comunitaria;
- cupón que cambia el precio final;
- oferta física que requiere validar existencia en sucursal.

Rojo significa “revisar/confirmar”, no “falsa”.

## Precios de $1, $0.99 y similares

El bot busca:
- precio <= $1 MXN;
- precio <= $10 MXN;
- descuentos >= 95%;
- en Walmart, terminaciones .01, .02 y .03 como señal adicional.

Las terminaciones .01/.02/.03 no se usan como prueba única. La referencia pública consultada describe esas terminaciones como fases de liquidación de Walmart, pero también advierte que no constituyen una ley y pueden variar. El bot por eso exige además una URL de producto y, para una alerta extrema verificada, intenta comprobar el precio directamente en la página.

## Cómo se verifican

Una liquidación extrema se marca como precio extremo verificado solo si:
1. La URL pertenece al dominio oficial de la tienda.
2. La página del producto responde.
3. El precio esperado aparece en la página.
4. El producto tiene un identificador/URL de producto, no una búsqueda genérica.

Si falla la verificación, el bot no afirma que el precio sea real; puede conservarlo como pista interna.

## Cómo se usan las comunidades

Las comunidades públicas sirven para descubrir candidatos rápidamente, pero no se consideran prueba. El bot debe volver a comprobar el producto en la tienda.

Facebook queda como fuente manual hasta disponer de una integración autorizada y estable; una URL de grupo no permite por sí sola leer sus publicaciones.

## Fuentes oficiales útiles

- Walmart: Liquidaciones.
- Chedraui: Precios de Liquidación.
- Sanborns: categorías con filtros de descuento.
- Mercado Libre: promociones y campañas de liquidación de stock.

Chedraui define su liquidación en línea como artículos en stock o de temporadas pasadas a un precio inferior al habitual y advierte que la disponibilidad puede ser de una pieza o más por sucursal. Mercado Libre documenta campañas específicas para liquidar stock y distintos tipos de promociones.
