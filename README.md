# Monitor de descuentos 80%-99% → Telegram

Este bot revisa Mercado Libre cada 15 minutos y te manda un mensaje de
Telegram cuando encuentra un producto con descuento de entre 80% y 99%.
Corre gratis en GitHub Actions, no necesitas dejar tu computadora prendida
ni pagar un servidor.

## Por qué empieza solo con Mercado Libre

Mercado Libre tiene una API pública que ya regresa el precio original y el
precio con descuento, así que es confiable y no se cae. Amazon, Liverpool,
Chedraui, Walmart y Coppel no tienen esa API pública: para monitorearlas
hay que leer directo su página web (scraping), lo cual es más frágil,
rompe cuando rediseñan el sitio y varias de ellas bloquean bots. Vale la
pena tenerlo funcionando bien con Mercado Libre primero y después, si
quieres, sumamos una tienda a la vez.

## Paso 1: Crear tu bot de Telegram

1. En Telegram, busca **@BotFather** y mándale `/newbot`.
2. Ponle un nombre y un usuario (tiene que terminar en `bot`, ej.
   `ofertas_juan_bot`).
3. BotFather te va a dar un **token** parecido a
   `123456789:ABCdefGhIJKlmnOpQRstuVwxYZ`. Guárdalo.

## Paso 2: Obtener tu Chat ID

1. Busca tu bot en Telegram (con el usuario que le pusiste) y mándale
   cualquier mensaje, por ejemplo "hola".
2. En tu navegador abre esta URL, cambiando `<TOKEN>` por el token del
   paso anterior:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Busca en el resultado algo como `"chat":{"id":123456789,...}` — ese
   número es tu **Chat ID**.

## Paso 3: Subir este proyecto a GitHub

1. Crea una cuenta en [github.com](https://github.com) si no tienes.
2. Crea un repositorio nuevo (puede ser privado), por ejemplo
   `monitor-ofertas`.
3. Sube todos los archivos de esta carpeta a ese repositorio (desde la
   web de GitHub puedes arrastrar los archivos con "Add file → Upload
   files").

## Paso 4: Configurar tus claves (secrets)

1. En tu repositorio, ve a **Settings → Secrets and variables →
   Actions**.
2. Crea un secret llamado `TELEGRAM_TOKEN` con el token del Paso 1.
3. Crea otro secret llamado `TELEGRAM_CHAT_ID` con el número del Paso 2.

## Paso 5: Activarlo

1. Ve a la pestaña **Actions** de tu repositorio.
2. Si te lo pide, da clic en "I understand my workflows, go ahead and
   enable them".
3. Entra al workflow "Monitor de descuentos" y dale **Run workflow**
   para probarlo manualmente una vez.
4. Si todo salió bien, deberías recibir un mensaje en Telegram (si en
   ese momento hay alguna oferta que cumpla el filtro) y a partir de
   ahí correrá solo cada 15 minutos.

## Personalizarlo

Abre `monitor_ofertas.py` y edita la lista `KEYWORDS_ML` con los
productos o categorías que te interesan (mientras más genérica la palabra,
más resultados revisa, pero también tarda más). También puedes cambiar
`MIN_DESCUENTO` y `MAX_DESCUENTO` si quieres otro rango.

## Nota importante

Los descuentos de 80%-99% casi siempre son errores de precio. Muchas
tiendas cancelan el pedido después de que pagas, así que trata cada aviso
como "vale la pena intentarlo", no como una compra garantizada.
