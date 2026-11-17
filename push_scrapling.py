#!/usr/bin/env python3
# push_scrapling.py
# Sube a stroks15/DexterH4ck-ofertas:
#   1) capa perimetro (transporte stealth, WAF, CAPTCHA, proxies, extracción multi-estrategia)
#   2) motor Scrapling primario con fallback al pipeline actual
#   3) parches de integración y correcciones de auditoría
#   4) tests en unittest, docs, workflows
#
#   python push_scrapling.py --zip     # escribe todo en disco + payload.zip
#   python push_scrapling.py           # rama + commit vía API + PR
#
# Extras: --list --dry-run --rama --no-pr --sin-parches --token --repo

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_REPO = "stroks15/DexterH4ck-ofertas"
DEFAULT_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
API = "https://api.github.com"
RAMA = "motor/scrapling-perimetro"
TITULO = "perimetro + motor Scrapling con fallback, integración y correcciones"
CUERPO = (
    "## Contenido\n"
    "1. `perimetro/`: cobertura declarativa de 17 tiendas en línea, transporte con escalado "
    "http -> stealth -> browser, rotación de proxies, detección de WAF/CAPTCHA y cooldown.\n"
    "2. `core/scrapling_motor.py`: motor primario (perfiles de dispositivo coherentes, sesiones async, "
    "timeout adaptativo por latencia EWMA, respeto de Retry-After) con degradación al método actual.\n"
    "3. `scrapers/scrapling_adaptador.py`: adaptadores por tienda que caen al scraper legacy.\n"
    "4. Parches: el motor se antepone a los adaptadores API y `TIENDAS_OBJETIVO` se amplía.\n"
    "5. Correcciones de auditoría (`docs/CORRECCIONES.md`).\n"
    "6. Tests en unittest, docs y workflows con caché de navegadores y verificación del motor.\n\n"
    "Los parches se aplican con ancla verificada: si el ancla no existe, el archivo queda intacto "
    "y el PR lo reporta en la sección Parches."
)


PAYLOAD = r'''
<<<FILE:.github/workflows/perimetro.yml>>>
# .github/workflows/perimetro.yml
# Diagnóstico manual del perímetro (no publica ofertas, solo verifica fuentes).
name: perimetro
on:
  workflow_dispatch:
    inputs:
      tienda:
        description: "id de tienda o 'todas'"
        required: false
        default: "todas"
      q:
        description: "término de búsqueda"
        required: false
        default: "liquidacion"
jobs:
  doctor:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements-perimetro.txt
      - run: scrapling install || true
      - run: python -m perimetro doctor
      - run: python -m perimetro probe --tienda "${{ inputs.tienda }}" --q "${{ inputs.q }}"
        env:
          PROXY_POOL: ${{ secrets.PROXY_POOL }}
          CAPTCHA_PROVIDER: ${{ secrets.CAPTCHA_PROVIDER }}
          CAPTCHA_API_KEY: ${{ secrets.CAPTCHA_API_KEY }}
<<<ENDFILE>>>
<<<FILE:.github/workflows/monitor.yml>>>
name: Monitor de descuentos

on:
  schedule:
    - cron: "7,22,37,52 * * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: monitor-ofertas
  cancel-in-progress: false

jobs:
  revisar:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Descargar repositorio
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          clean: true

      - name: Configurar Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Instalar dependencias
        shell: bash
        run: |
          set -euo pipefail
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then
            python -m pip install -r requirements.txt
          else
            echo "::error::No existe requirements.txt"
            exit 1
          fi
          if [ -f requirements-browser.txt ]; then
            python -m pip install -r requirements-browser.txt
          else
            echo "::notice::requirements-browser.txt no existe; se omite la instalación de dependencias de navegador."
          fi
          if [ -f requirements-scrapling.txt ]; then
            python -m pip install -r requirements-scrapling.txt
          fi
          if [ -f requirements-perimetro.txt ]; then
            python -m pip install -r requirements-perimetro.txt
          fi

      - name: Cachear navegadores para el motor
        uses: actions/cache@v4
        id: motor-cache
        with:
          path: |
            ~/.cache/ms-playwright
            ~/.cache/scrapling
          key: ${{ runner.os }}-scrapling-${{ hashFiles('requirements-scrapling.txt') }}

      - name: Instalar navegadores del motor
        if: steps.motor-cache.outputs.cache-hit != 'true'
        continue-on-error: true
        run: |
          python -m playwright install chromium || true
          scrapling install --force || scrapling install || true

      - name: Verificar Scrapling (sesiones HTTP respetuosas)
        shell: bash
        run: |
          python -c "from scrapling.spiders import Spider; from scrapling.fetchers import FetcherSession; print('Scrapling Spider disponible')"

      - name: Verificar motor con fallback
        continue-on-error: true
        shell: bash
        run: python tools/verificar_motor.py

      - name: Verificar secretos de Telegram
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          test -n "$TELEGRAM_TOKEN" || { echo "::error::Falta el secret TELEGRAM_TOKEN"; exit 1; }
          test -n "$TELEGRAM_CHAT_ID" || { echo "::error::Falta el secret TELEGRAM_CHAT_ID"; exit 1; }
          echo "Secretos de Telegram detectados correctamente."

      - name: Validar destino real del grupo Ofertas en Telegram
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        shell: bash
        run: |
          python - <<'PY'
          import json
          import os
          import urllib.error
          import urllib.request

          token = os.environ['TELEGRAM_TOKEN']
          chat_id = os.environ['TELEGRAM_CHAT_ID']
          url = 'https://api.telegram.org/bot' + token + '/getChat?chat_id=' + chat_id
          try:
              with urllib.request.urlopen(url, timeout=20) as response:
                  payload = json.loads(response.read().decode('utf-8'))
          except urllib.error.HTTPError as exc:
              body = exc.read().decode('utf-8', 'replace')
              try:
                  detail = json.loads(body).get('description', body)
              except Exception:
                  detail = body
              raise SystemExit('Telegram getChat rechazó TELEGRAM_CHAT_ID (HTTP %s): %s' % (exc.code, detail))
          if not payload.get('ok'):
              raise SystemExit('Telegram getChat falló: ' + str(payload.get('description', 'respuesta no válida')))
          chat = payload.get('result', {})
          actual_id = str(chat.get('id', ''))
          title = chat.get('title', '')
          kind = chat.get('type', '')
          print('Telegram reconoce el destino: id=%s, título=%r, tipo=%r' % (actual_id, title, kind))
          if actual_id != chat_id:
              raise SystemExit('El ID devuelto por Telegram no coincide con TELEGRAM_CHAT_ID.')
          if kind not in ('group', 'supergroup'):
              raise SystemExit('El destino de Telegram no es un grupo/supergrupo. No se enviarán alertas.')
          PY
      - name: Verificar credenciales de IA
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
        run: |
          test -n "$GEMINI_API_KEY" || echo "::warning::GEMINI_API_KEY no está configurada; el monitor funcionará sin Gemini."
          test -n "$GROQ_API_KEY" || echo "::warning::GROQ_API_KEY no está configurada; el monitor funcionará sin Groq."

      - name: Diagnosticar acceso real a APIs de IA
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
        shell: bash
        run: |
          set +e
          if [ -z "$GEMINI_API_KEY" ]; then
            echo "::warning::Gemini: secret ausente en Actions."
          else
            code=$(curl -sS -o /tmp/gemini-models.json -w "%{http_code}" --max-time 15               "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY")
            case "$code" in
              200) echo "Gemini: clave presente y API accesible (HTTP 200)." ;;
              400|401|403) echo "::error::Gemini: clave presente pero rechazada o solicitud inválida (HTTP $code)." ;;
              429) echo "::warning::Gemini: clave presente, pero límite/cuota alcanzado (HTTP 429)." ;;
              *) echo "::warning::Gemini: API respondió HTTP $code." ;;
            esac
          fi
          if [ -z "$GROQ_API_KEY" ]; then
            echo "::warning::Groq: secret ausente en Actions."
          else
            code=$(curl -sS -o /tmp/groq-models.json -w "%{http_code}" --max-time 15               -H "Authorization: Bearer $GROQ_API_KEY"               -H "Content-Type: application/json"               "https://api.groq.com/openai/v1/models")
            case "$code" in
              200) echo "Groq: clave presente y API accesible (HTTP 200)." ;;
              401|403) echo "::error::Groq: clave presente pero rechazada (HTTP $code)." ;;
              429) echo "::warning::Groq: clave presente, pero límite/cuota alcanzado (HTTP 429)." ;;
              *) echo "::warning::Groq: API respondió HTTP $code." ;;
            esac
          fi

      - name: Verificar sesión de usuario de Telegram (opcional)
        env:
          TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
          TELEGRAM_SESSION_STRING: ${{ secrets.TELEGRAM_SESSION_STRING }}
        run: |
          if [ -n "$TELEGRAM_API_ID" ] && [ -n "$TELEGRAM_API_HASH" ] && [ -n "$TELEGRAM_SESSION_STRING" ]; then
            echo "Sesión de usuario de Telegram detectada; se leerá el grupo privado."
          else
            echo "::notice::Faltan TELEGRAM_API_ID/TELEGRAM_API_HASH/TELEGRAM_SESSION_STRING; el grupo privado se omite (los canales públicos siguen)."
          fi

      - name: Verificar token opcional de Mercado Libre
        env:
          MERCADOLIBRE_ACCESS_TOKEN: ${{ secrets.MERCADOLIBRE_ACCESS_TOKEN }}
        run: |
          if [ -n "$MERCADOLIBRE_ACCESS_TOKEN" ]; then
            echo "Token de Mercado Libre detectado; se habilitará enriquecimiento oficial de precios."
          else
            echo "::notice::MERCADOLIBRE_ACCESS_TOKEN no configurado; Mercado Libre seguirá usando búsqueda pública + historial."
          fi

      - name: Validar sintaxis Python
        run: python -m compileall -q .

      - name: Pruebas deterministas de liquidaciones extremas
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python -m unittest discover -s tests -p "test_*.py" -v

      - name: Preflight - detectar errores antes del monitor
        env:
          APPS_SCRIPT_PROXY_URL: ${{ secrets.APPS_SCRIPT_PROXY_URL }}
        run: |
          python preflight.py
          cat preflight.env >> "$GITHUB_ENV"

      - name: Diagnosticar endpoints de tiendas
        env:
          WALMART_GRAPHQL_URL: ${{ vars.WALMART_GRAPHQL_URL }}
          BODEGA_GRAPHQL_URL: ${{ vars.BODEGA_GRAPHQL_URL }}
          CHEDRAUI_VTEX_ENDPOINT: ${{ vars.CHEDRAUI_VTEX_ENDPOINT }}
          SORIANA_API_ENDPOINT: ${{ vars.SORIANA_API_ENDPOINT }}
          COPPEL_API_ENDPOINT: ${{ vars.COPPEL_API_ENDPOINT }}
          SUBURBIA_API_ENDPOINT: ${{ vars.SUBURBIA_API_ENDPOINT }}
          AMAZON_API_ENDPOINT: ${{ vars.AMAZON_API_ENDPOINT }}
        run: python tools/diagnostico_fuentes.py

      - name: Guardar diagnóstico de fuentes
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: diagnostico-fuentes
          path: diagnostico_fuentes.json
          if-no-files-found: ignore

      - name: Guardar diagnóstico de preflight
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: preflight-health
          path: preflight_health.json
          if-no-files-found: ignore

      - name: Preparar contexto RAG con Firecrawl (opcional)
        continue-on-error: true
        env:
          FIRECRAWL_API_KEY: ${{ secrets.FIRECRAWL_API_KEY }}
          FIRECRAWL_RAG_URLS: ${{ vars.FIRECRAWL_RAG_URLS }}
        run: python tools/firecrawl_rag_export.py

      - name: Conservar contexto RAG de Firecrawl
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: firecrawl-rag-context
          path: .cache/firecrawl/rag_context.jsonl
          if-no-files-found: ignore

      - name: Ejecutar monitor
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          GRUPO_FUENTE_CHAT_ID: ${{ secrets.GRUPO_FUENTE_CHAT_ID }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GEMINI_MODEL: gemini-3.8-flash
          GEMINI_MAX_CALLS: "6"
          GEMINI_MIN_DELAY: "3.0"
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          GROQ_MODEL: openai/gpt-oss-20b
          GROQ_MAX_CALLS: "8"
          GROQ_MIN_DELAY: "1.5"
          MERCADOLIBRE_ACCESS_TOKEN: ${{ secrets.MERCADOLIBRE_ACCESS_TOKEN }}
          APPS_SCRIPT_PROXY_URL: ${{ secrets.APPS_SCRIPT_PROXY_URL }}
          TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
          TELEGRAM_SESSION_STRING: ${{ secrets.TELEGRAM_SESSION_STRING }}
          TELEGRAM_WALMART_INVITE: ${{ secrets.TELEGRAM_WALMART_INVITE }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SECRET_KEY }}
          FIRECRAWL_API_KEY: ${{ secrets.FIRECRAWL_API_KEY }}
          FIRECRAWL_RAG_URLS: ${{ vars.FIRECRAWL_RAG_URLS }}
          ML_PRICE_LOOKUPS: "40"
          ML_OFFICIAL_ONLY: "false"
          ML_API_LIMIT: "50"
          ML_API_TIMEOUT: "12"
          ML_API_DELAY: "0.20"
          ML_API_REQUIRE_TOKEN: "false"
          PROMODESCUENTOS_RSS_URL: "https://promodescuentos.com/rss"
          WALMART_GRAPHQL_URL: ${{ vars.WALMART_GRAPHQL_URL }}
          BODEGA_GRAPHQL_URL: ${{ vars.BODEGA_GRAPHQL_URL }}
          WALMART_STORE_ID: ${{ vars.WALMART_STORE_ID }}
          WALMART_TENANT: ${{ vars.WALMART_TENANT }}
          BODEGA_STORE_ID: ${{ vars.BODEGA_STORE_ID }}
          CHEDRAUI_VTEX_ENDPOINT: ${{ vars.CHEDRAUI_VTEX_ENDPOINT }}
          SORIANA_API_ENDPOINT: ${{ vars.SORIANA_API_ENDPOINT }}
          COPPEL_API_ENDPOINT: ${{ vars.COPPEL_API_ENDPOINT }}
          COPPEL_VTEX_ENDPOINT: ${{ vars.COPPEL_VTEX_ENDPOINT }}
          SUBURBIA_API_ENDPOINT: ${{ vars.SUBURBIA_API_ENDPOINT }}
          AMAZON_API_ENDPOINT: ${{ vars.AMAZON_API_ENDPOINT }}
          LIVERPOOL_API_ENDPOINT: ${{ vars.LIVERPOOL_API_ENDPOINT }}
          LIVERPOOL_VTEX_ENDPOINT: ${{ vars.LIVERPOOL_VTEX_ENDPOINT }}
          ML_QUERIES: ${{ vars.ML_QUERIES }}
          WALMART_QUERIES: ${{ vars.WALMART_QUERIES }}
          WALMART_PAGES: ${{ vars.WALMART_PAGES }}
          BODEGA_QUERIES: ${{ vars.BODEGA_QUERIES }}
          BODEGA_PAGES: ${{ vars.BODEGA_PAGES }}
          CHEDRAUI_QUERIES: ${{ vars.CHEDRAUI_QUERIES }}
          CHEDRAUI_VTEX_PAGES: ${{ vars.CHEDRAUI_VTEX_PAGES }}
          SORIANA_QUERIES: ${{ vars.SORIANA_QUERIES }}
          SORIANA_PAGES: ${{ vars.SORIANA_PAGES }}
          LEGACY_SEARCH_URLS_JSON: ${{ vars.LEGACY_SEARCH_URLS_JSON }}
          NETWORK_BROWSER_ENABLED: "true"
          NETWORK_BROWSER_SOURCES: ${{ vars.NETWORK_BROWSER_SOURCES }}
          NETWORK_BROWSER_WAIT_MS: "6500"
          NETWORK_BROWSER_MAX_RESPONSES: "20"
          HYBRID_DISCOVERY_ENABLED: ${{ vars.HYBRID_DISCOVERY_ENABLED || 'true' }}
          HYBRID_STRUCTURED_SOURCES: ${{ vars.HYBRID_STRUCTURED_SOURCES }}
          HYBRID_PLAYWRIGHT_SOURCES: ${{ vars.HYBRID_PLAYWRIGHT_SOURCES }}
          HYBRID_PLAYWRIGHT_WAIT_MS: "2500"
          TELEGRAM_STICKER_LIQUIDACION: ${{ vars.TELEGRAM_STICKER_LIQUIDACION }}
          # Motor Scrapling: primario, con fallback al pipeline actual.
          SCRAPLING_MOTOR: ${{ vars.SCRAPLING_MOTOR || 'auto' }}
          SCRAPLING_CONCURRENCIA: "4"
          SCRAPLING_PERFILES: ${{ vars.SCRAPLING_PERFILES || 'desktop_chrome_win,android_chrome,movil_safari_ios' }}
          SCRAPLING_ROTAR_CADA: "3"
          SCRAPLING_SOLVE_CLOUDFLARE: ${{ vars.SCRAPLING_SOLVE_CLOUDFLARE || 'auto' }}
          SCRAPLING_FALLBACK: "true"
          PROXY_POOL: ${{ secrets.PROXY_POOL }}
          PROXY_ESTRATEGIA: ${{ vars.PROXY_ESTRATEGIA || 'sticky' }}
          CAPTCHA_PROVIDER: ${{ secrets.CAPTCHA_PROVIDER }}
          CAPTCHA_API_KEY: ${{ secrets.CAPTCHA_API_KEY }}
        run: python monitor_ofertas.py

      - name: Guardar historial con commit directo seguro
        shell: bash
        run: |
          set -euo pipefail
          if [ ! -f historial_ofertas.json ]; then
            echo "No hay historial generado."
            exit 0
          fi
          git config user.name "bot-ofertas"
          git config user.email "bot-ofertas@users.noreply.github.com"
          git add historial_ofertas.json
          for archivo in fuentes_cooldown.json tg_grupo_offset.json motor_cooldown.json perimetro_cooldown.json; do
            if [ -f "$archivo" ]; then git add "$archivo"; fi
          done
          if git diff --cached --quiet; then
            echo "Historial sin cambios."
            exit 0
          fi
          git commit -m "Actualiza historial de ofertas [skip ci]"

          for intento in {1..5}; do
            echo "Intento de Push de historial $intento/5..."
            git fetch origin main

            if git push origin HEAD:main; then
              echo "Historial actualizado."
              exit 0
            fi

            echo "Push concurrente; sincronizando intento $intento..."
            git reset --soft HEAD~1
            git checkout -- historial_ofertas.json
            git fetch origin main
            git merge --ff-only origin/main 2>/dev/null || {
              echo "La rama avanzó mientras se ejecutaba el monitor; se conserva el historial local como siguiente ciclo."
              exit 0
            }
            sleep $((intento * 2))
          done
          echo "::warning::No se pudo publicar el historial en este ciclo; el monitor sí terminó correctamente."
          exit 0

      - name: Guardar diagnóstico final del monitor
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: monitor-health-final
          path: |
            preflight_health.json
            monitor_health.json
            motor_health.json
          if-no-files-found: ignore
<<<ENDFILE>>>
<<<FILE:config/perimetro_tiendas.json>>>
{
  "version": 1,
  "nota": "Perímetro de tiendas en línea. 'estrategias' es una lista ORDENADA: el adaptador prueba la primera y si no devuelve filas pasa a la siguiente, de modo que un cambio de plataforma no rompe la tienda. Sin endpoints internos inventados: se usan rutas públicas de catálogo/búsqueda.",
  "reglas": {"max_paginas": 2, "max_por_tienda": 40, "rps_por_host": 0.5},
  "tiendas": [
    {"id": "walmart", "nombre": "Walmart MX", "home": "https://www.walmart.com.mx", "busqueda": "https://www.walmart.com.mx/search?q={q}", "estrategias": ["nextdata", "graphql", "html"], "requiere_js": true, "prioridad": 10, "selectores": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]", "link": "a[href]"}, "notas": "Resultados embebidos en __NEXT_DATA__; el GraphQL interno necesita storeId."},
    {"id": "bodega", "nombre": "Bodega Aurrera", "home": "https://www.bodegaaurrera.com.mx", "busqueda": "https://www.bodegaaurrera.com.mx/search?q={q}", "estrategias": ["nextdata", "graphql", "html"], "requiere_js": true, "prioridad": 10, "selectores": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]", "link": "a[href]"}, "notas": "Misma plataforma que Walmart de México."},
    {"id": "sams", "nombre": "Sam's Club", "home": "https://www.samsclub.com.mx", "busqueda": "https://www.samsclub.com.mx/search?q={q}", "estrategias": ["nextdata", "html"], "requiere_js": true, "prioridad": 8, "selectores": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]", "link": "a[href]"}, "notas": "Walmart de México; suele exigir sesión para precio de socio."},
    {"id": "chedraui", "nombre": "Chedraui", "home": "https://www.chedraui.com.mx", "busqueda": "https://www.chedraui.com.mx/search?q={q}", "vtex_host": "https://www.chedraui.com.mx", "estrategias": ["vtex", "html"], "prioridad": 10, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}, "notas": "Catálogo público VTEX ya usado por scrapers/vtex_stores.py."},
    {"id": "soriana", "nombre": "Soriana", "home": "https://www.soriana.com", "busqueda": "https://www.soriana.com/search?q={q}", "vtex_host": "https://www.soriana.com", "estrategias": ["vtex", "html"], "prioridad": 9, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}},
    {"id": "coppel", "nombre": "Coppel", "home": "https://www.coppel.com", "busqueda": "https://www.coppel.com/buscar?q={q}", "vtex_host": "https://www.coppel.com", "estrategias": ["vtex", "html"], "prioridad": 9, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}},
    {"id": "elektra", "nombre": "Elektra", "home": "https://www.elektra.mx", "busqueda": "https://www.elektra.mx/search?q={q}", "vtex_host": "https://www.elektra.mx", "estrategias": ["vtex", "html"], "prioridad": 9, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}, "notas": "Verificado: /api/catalog_system/pub/products/search responde JSON VTEX."},
    {"id": "liverpool", "nombre": "Liverpool", "home": "https://www.liverpool.com.mx", "busqueda": "https://www.liverpool.com.mx/tienda?s={q}", "vtex_host": "https://www.liverpool.com.mx", "estrategias": ["vtex", "jsonld", "html"], "requiere_js": true, "prioridad": 9, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}},
    {"id": "suburbia", "nombre": "Suburbia", "home": "https://www.suburbia.com.mx", "busqueda": "https://www.suburbia.com.mx/tienda?s={q}", "vtex_host": "https://www.suburbia.com.mx", "estrategias": ["vtex", "jsonld", "html"], "requiere_js": true, "prioridad": 8, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}},
    {"id": "sears", "nombre": "Sears", "home": "https://www.sears.com.mx", "busqueda": "https://www.sears.com.mx/tienda?s={q}", "estrategias": ["jsonld", "html"], "prioridad": 7, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}, "notas": "No expone la ruta VTEX; se resuelve por JSON-LD/HTML."},
    {"id": "nike", "nombre": "Nike", "home": "https://www.nike.com", "busqueda": "https://www.nike.com/mx/w?q={q}", "estrategias": ["jsonld", "html"], "prioridad": 8, "selectores": {"card": "div.product-card", "title": "div.product-card__title", "price": "div.product-price", "link": "a.product-card__link-overlay"}, "notas": "Verificado: HTML SSR con precio tachado y porcentaje de descuento en la tarjeta."},
    {"id": "adidas", "nombre": "Adidas", "home": "https://www.adidas.mx", "busqueda": "https://www.adidas.mx/search?q={q}", "estrategias": ["jsonld", "html"], "requiere_js": true, "prioridad": 8, "selectores": {"card": "div[data-auto-id=\"product-card\"]", "title": "div[data-auto-id=\"product-card-description\"] span", "price": "div[data-auto-id=\"product-price\"]", "link": "a[href]"}},
    {"id": "palacio", "nombre": "Palacio de Hierro", "home": "https://www.elpalaciodehierro.com", "busqueda": "https://www.elpalaciodehierro.com/buscar?q={q}", "sfcc_site": "Sites-palacio-MX-Site", "estrategias": ["sfcc", "jsonld"], "prioridad": 7, "selectores": {"card": "div.product-tile", "title": "a.link", "price": "span.product-sales-price", "link": "a.link"}, "notas": "Salesforce Commerce (Demandware). Usa SearchServices-GetSuggestions y Search-Show."},
    {"id": "homedepot", "nombre": "Home Depot", "home": "https://www.homedepot.com.mx", "busqueda": "https://www.homedepot.com.mx/buscar?q={q}", "estrategias": ["nextdata", "jsonld", "html"], "requiere_js": true, "prioridad": 7, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}},
    {"id": "officedepot", "nombre": "Office Depot", "home": "https://www.officedepot.com.mx", "busqueda": "https://www.officedepot.com.mx/search?q={q}", "sfcc_site": "Sites-officedepot-Site", "estrategias": ["sfcc", "jsonld"], "prioridad": 7, "selectores": {"card": "div.product-tile", "title": "a.link", "price": "span.product-sales-price", "link": "a.link"}, "notas": "Salesforce Commerce (theme-officedepot en /_ui/responsive)."},
    {"id": "lacomer", "nombre": "La Comer", "home": "https://www.lacomer.com.mx", "busqueda": "https://www.lacomer.com.mx/lacomer/#!/buscar?q={q}", "vtex_host": "https://www.lacomer.com.mx", "estrategias": ["vtex", "html"], "requiere_js": true, "prioridad": 7, "selectores": {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}, "notas": "SPA con hash; se resuelve por la ruta VTEX cuando responde."},
    {"id": "amazon", "nombre": "Amazon MX", "home": "https://www.amazon.com.mx", "busqueda": "https://www.amazon.com.mx/s?k={q}", "estrategias": ["amazon", "jsonld", "html"], "prioridad": 9, "selectores": {"card": "div[data-component-type=\"s-search-result\"]", "title": "h2 span", "price": "span.a-price-whole", "link": "h2 a", "antes": "span.a-price.a-text-price span.a-offscreen"}, "notas": "Bloquea rápido por IP: la rotación y el cooldown son obligatorios aquí."}
  ]
}
<<<ENDFILE>>>
<<<FILE:config/scrapling_motor.json>>>
{
  "version": 1,
  "nota": "Parámetros del motor. Sobrescribible por entorno (SCRAPLING_*). Perfiles coherentes: UA, sec-ch-ua, plataforma, viewport y DPR casan entre sí.",
  "modo": "auto",
  "concurrencia": 4,
  "rotar_perfil_cada": 3,
  "fallback_habilitado": true,
  "reintentos": 2,
  "presupuesto": {"base_ms": 20000, "min_ms": 8000, "max_ms": 60000, "factor_ewma": 1.8, "alfa_ewma": 0.3, "tope_retry_after_s": 45},
  "esperas": {"tras_carga_ms": 1200, "jitter_ms": 700},
  "perfiles": [
    {"id": "desktop_chrome_win", "dispositivo": "desktop", "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "plataforma": "Windows", "sec_ch_ua": "\"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\", \"Google Chrome\";v=\"131\"", "movil": false, "viewport": {"width": 1536, "height": 864}, "dpr": 1.0, "impersonate": "chrome"},
    {"id": "desktop_chrome_mac", "dispositivo": "desktop", "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "plataforma": "macOS", "sec_ch_ua": "\"Chromium\";v=\"130\", \"Not_A Brand\";v=\"24\", \"Google Chrome\";v=\"130\"", "movil": false, "viewport": {"width": 1440, "height": 900}, "dpr": 2.0, "impersonate": "chrome130"},
    {"id": "android_chrome", "dispositivo": "movil", "ua": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36", "plataforma": "Android", "sec_ch_ua": "\"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\", \"Google Chrome\";v=\"131\"", "movil": true, "viewport": {"width": 412, "height": 915}, "dpr": 2.625, "impersonate": "chrome_android"},
    {"id": "movil_safari_ios", "dispositivo": "movil", "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "plataforma": "iOS", "sec_ch_ua": "", "movil": true, "viewport": {"width": 393, "height": 852}, "dpr": 3.0, "impersonate": "safari_ios"},
    {"id": "tablet_ipad", "dispositivo": "tablet", "ua": "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "plataforma": "iOS", "sec_ch_ua": "", "movil": true, "viewport": {"width": 1024, "height": 1366}, "dpr": 2.0, "impersonate": "safari_ios"}
  ],
  "perfil_por_host": {"www.amazon.com.mx": "desktop_chrome_win", "www.nike.com": "desktop_chrome_mac", "www.adidas.mx": "android_chrome", "www.lacomer.com.mx": "android_chrome"},
  "dominios": {
    "requieren_navegador": ["www.walmart.com.mx", "www.bodegaaurrera.com.mx", "www.samsclub.com.mx", "www.homedepot.com.mx", "www.lacomer.com.mx", "www.adidas.mx", "www.liverpool.com.mx", "www.suburbia.com.mx"],
    "nunca_navegador": ["api.mercadolibre.com", "api.telegram.org", "promodescuentos.com"],
    "solve_cloudflare": ["www.amazon.com.mx", "www.nike.com"]
  },
  "tiendas": {
    "walmart": {"perfil": "desktop_chrome_win", "navegador": true, "url": "https://www.walmart.com.mx/search?q={q}"},
    "bodega": {"perfil": "desktop_chrome_win", "navegador": true, "url": "https://www.bodegaaurrera.com.mx/search?q={q}"},
    "sams": {"perfil": "desktop_chrome_win", "navegador": true, "url": "https://www.samsclub.com.mx/search?q={q}"},
    "chedraui": {"perfil": "desktop_chrome_mac", "navegador": false, "url": "https://www.chedraui.com.mx/search?q={q}"},
    "soriana": {"perfil": "desktop_chrome_win", "navegador": false, "url": "https://www.soriana.com/search?q={q}"},
    "coppel": {"perfil": "desktop_chrome_mac", "navegador": false, "url": "https://www.coppel.com/buscar?q={q}"},
    "elektra": {"perfil": "desktop_chrome_win", "navegador": false, "url": "https://www.elektra.mx/search?q={q}"},
    "liverpool": {"perfil": "desktop_chrome_mac", "navegador": true, "url": "https://www.liverpool.com.mx/tienda?s={q}"},
    "suburbia": {"perfil": "android_chrome", "navegador": true, "url": "https://www.suburbia.com.mx/tienda?s={q}"},
    "sears": {"perfil": "desktop_chrome_win", "navegador": false, "url": "https://www.sears.com.mx/tienda?s={q}"},
    "nike": {"perfil": "desktop_chrome_mac", "navegador": false, "url": "https://www.nike.com/mx/w?q={q}"},
    "adidas": {"perfil": "android_chrome", "navegador": true, "url": "https://www.adidas.mx/search?q={q}"},
    "palacio": {"perfil": "desktop_chrome_mac", "navegador": false, "url": "https://www.elpalaciodehierro.com/buscar?q={q}"},
    "homedepot": {"perfil": "desktop_chrome_win", "navegador": true, "url": "https://www.homedepot.com.mx/buscar?q={q}"},
    "officedepot": {"perfil": "desktop_chrome_win", "navegador": false, "url": "https://www.officedepot.com.mx/search?q={q}"},
    "lacomer": {"perfil": "android_chrome", "navegador": true, "url": "https://www.lacomer.com.mx/lacomer/#!/buscar?q={q}"},
    "amazon": {"perfil": "desktop_chrome_win", "navegador": false, "url": "https://www.amazon.com.mx/s?k={q}"}
  }
}
<<<ENDFILE>>>
<<<FILE:config/tiendas_scraping_plan.json>>>
{
  "tiendas": ["Walmart Mexico", "Bodega Aurrera", "Soriana", "Chedraui", "Sam's Club", "Coppel", "Elektra", "Liverpool", "Suburbia", "Sears", "Nike", "Adidas", "Palacio de Hierro", "Home Depot", "Office Depot", "La Comer", "Amazon Mexico", "Mercado Libre Mexico"],
  "prioridad": "liquidaciones",
  "objetivo": "detectar descuentos reales y evitar duplicados",
  "capa": "perimetro",
  "nota": "Las 17 tiendas en línea se ejecutan desde config/perimetro_tiendas.json con la capa stealth; Mercado Libre sigue por scrapers/mercado_libre_api.py."
}
<<<ENDFILE>>>
<<<FILE:perimetro/__init__.py>>>
# perimetro/__init__.py
from __future__ import annotations

__version__ = "1.0.0"

from .registry import TIENDAS, TIENDAS_OBJETIVO, tienda, tiendas_activas  # noqa: E402
from .adaptadores import PerimetroScraper, adaptadores  # noqa: E402

__all__ = ["TIENDAS", "TIENDAS_OBJETIVO", "tienda", "tiendas_activas", "PerimetroScraper", "adaptadores", "__version__"]
<<<ENDFILE>>>
<<<FILE:perimetro/__main__.py>>>
# perimetro/__main__.py
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
<<<ENDFILE>>>
<<<FILE:perimetro/registry.py>>>
# perimetro/registry.py
# Registro del perímetro. La data vive en config/perimetro_tiendas.json para poder
# ajustar una tienda sin tocar código; aquí solo se valida y se expone tipada.
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

RAIZ = Path(__file__).resolve().parent.parent
RUTA_DEFECTO = RAIZ / "config" / "perimetro_tiendas.json"

ESTRATEGIAS_VALIDAS = {"vtex", "sfcc", "nextdata", "graphql", "amazon", "jsonld", "html"}


@dataclass(frozen=True)
class Tienda:
    id: str
    nombre: str
    home: str
    estrategias: tuple[str, ...]
    busqueda: str = ""
    vtex_host: str = ""
    sfcc_site: str = ""
    requiere_js: bool = False
    prioridad: int = 5
    notas: str = ""
    selectores: dict[str, str] = field(default_factory=dict)
    activa: bool = True

    def url_busqueda(self, termino: str) -> str:
        if not self.busqueda:
            return ""
        return self.busqueda.replace("{q}", quote_plus(termino))

    @property
    def host(self) -> str:
        partes = self.home.split("//", 1)
        return partes[1].split("/", 1)[0] if len(partes) == 2 else self.home

    def a_dict(self) -> dict[str, Any]:
        return {"id": self.id, "nombre": self.nombre, "home": self.home, "estrategias": list(self.estrategias), "requiere_js": self.requiere_js, "prioridad": self.prioridad, "activa": self.activa}


def _ruta() -> Path:
    return Path(os.environ.get("PERIMETRO_CONFIG", "") or RUTA_DEFECTO)


def _normalizar_estrategias(bruto: Any) -> tuple[str, ...]:
    if isinstance(bruto, str):
        bruto = [bruto]
    if not isinstance(bruto, list):
        return ("html",)
    limpias = [str(e).strip().lower() for e in bruto if str(e).strip()]
    validas = tuple(e for e in limpias if e in ESTRATEGIAS_VALIDAS)
    return validas or ("html",)


def _tienda_desde(bruto: dict[str, Any]) -> Tienda | None:
    ident = str(bruto.get("id") or "").strip().lower()
    nombre = str(bruto.get("nombre") or "").strip()
    home = str(bruto.get("home") or "").strip()
    if not ident or not nombre or not home.startswith("http"):
        return None
    selectores = bruto.get("selectores") or {}
    if not isinstance(selectores, dict):
        selectores = {}
    try:
        prioridad = int(bruto.get("prioridad", 5))
    except (TypeError, ValueError):
        prioridad = 5
    return Tienda(
        id=ident,
        nombre=nombre,
        home=home,
        estrategias=_normalizar_estrategias(bruto.get("estrategias")),
        busqueda=str(bruto.get("busqueda") or ""),
        vtex_host=str(bruto.get("vtex_host") or "").rstrip("/"),
        sfcc_site=str(bruto.get("sfcc_site") or ""),
        requiere_js=bool(bruto.get("requiere_js")),
        prioridad=max(1, min(10, prioridad)),
        notas=str(bruto.get("notas") or ""),
        selectores={str(k): str(v) for k, v in selectores.items() if v},
        activa=bruto.get("activa", True) is not False,
    )


@lru_cache(maxsize=4)
def _cargar(ruta_txt: str) -> tuple[Tienda, ...]:
    ruta = Path(ruta_txt)
    if not ruta.is_file():
        return ()
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    tiendas: dict[str, Tienda] = {}
    for bruto in data.get("tiendas") or []:
        if not isinstance(bruto, dict):
            continue
        leida = _tienda_desde(bruto)
        if leida and leida.id not in tiendas:
            tiendas[leida.id] = leida
    return tuple(sorted(tiendas.values(), key=lambda t: (-t.prioridad, t.id)))


def recargar() -> tuple[Tienda, ...]:
    _cargar.cache_clear()
    return _cargar(str(_ruta()))


TIENDAS: tuple[Tienda, ...] = _cargar(str(_ruta()))
TIENDAS_OBJETIVO: tuple[str, ...] = tuple(t.nombre for t in TIENDAS)


def tiendas_activas() -> list[Tienda]:
    return [t for t in recargar() if t.activa]


def tienda(ident: str) -> Tienda | None:
    clave = str(ident or "").strip().lower()
    if not clave:
        return None
    for t in recargar():
        if t.id == clave or t.nombre.lower() == clave:
            return t
    return None


def seleccionar(nombres: list[str] | None) -> list[Tienda]:
    activas = tiendas_activas()
    if not nombres:
        return activas
    pedidos = [n.strip().lower() for n in nombres if n.strip()]
    if not pedidos or "todas" in pedidos or "all" in pedidos:
        return activas
    indice = {t.id: t for t in activas}
    salida: list[Tienda] = []
    for pedido in pedidos:
        encontrada = indice.get(pedido) or tienda(pedido)
        if encontrada and encontrada.activa and encontrada not in salida:
            salida.append(encontrada)
    return salida
<<<ENDFILE>>>
<<<FILE:perimetro/ritmo.py>>>
# perimetro/ritmo.py
from __future__ import annotations

import os
import random
import threading
import time


def _env_float(nombre: str, defecto: float, minimo: float, maximo: float) -> float:
    try:
        valor = float(os.environ.get(nombre, "") or defecto)
    except (TypeError, ValueError):
        return defecto
    return max(minimo, min(maximo, valor))


class Ritmo:
    def __init__(self, rps: float | None = None, rafaga: float = 3.0) -> None:
        self.rps = rps if rps is not None else _env_float("PERIMETRO_RPS", 0.5, 0.05, 10.0)
        self.rafaga = max(1.0, rafaga)
        self._cubos: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def _espera(self, host: str) -> float:
        ahora = time.monotonic()
        with self._lock:
            fichas, marca = self._cubos.get(host, (self.rafaga, ahora))
            fichas = min(self.rafaga, fichas + (ahora - marca) * self.rps)
            if fichas >= 1.0:
                self._cubos[host] = (fichas - 1.0, ahora)
                return 0.0
            falta = (1.0 - fichas) / self.rps
            self._cubos[host] = (0.0, ahora + falta)
            return falta

    def esperar(self, host: str) -> float:
        espera = self._espera(host)
        if espera > 0:
            time.sleep(espera + random.uniform(0.05, 0.35))
        return espera

    def penalizar(self, host: str, segundos: float) -> None:
        with self._lock:
            self._cubos[host] = (0.0, time.monotonic() + max(0.0, segundos))

    @staticmethod
    def backoff(intento: int, base: float = 1.2, tope: float = 20.0) -> float:
        return min(tope, base * (2 ** max(0, intento))) + random.uniform(0.2, 0.9)
<<<ENDFILE>>>
<<<FILE:perimetro/proxies.py>>>
# perimetro/proxies.py
from __future__ import annotations

import json
import os
import random
import threading
import time
from urllib.parse import unquote, urlsplit

CASTIGO_SEG = 180.0


def _desde_url(bruto: str) -> dict[str, str] | None:
    texto = bruto.strip()
    if not texto:
        return None
    if "://" not in texto:
        texto = "http://" + texto
    partes = urlsplit(texto)
    if not partes.hostname:
        return None
    servidor = f"{partes.scheme}://{partes.hostname}"
    if partes.port:
        servidor += f":{partes.port}"
    proxy = {"server": servidor}
    if partes.username:
        proxy["username"] = unquote(partes.username)
        proxy["password"] = unquote(partes.password or "")
    return proxy


def parsear(bruto: str) -> list[dict[str, str]]:
    crudo = (bruto or "").strip()
    if not crudo:
        return []
    if crudo.startswith("["):
        try:
            data = json.loads(crudo)
        except ValueError:
            data = []
        salida = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and item.get("server"):
                limpio = {"server": str(item["server"])}
                if item.get("username"):
                    limpio["username"] = str(item["username"])
                    limpio["password"] = str(item.get("password") or "")
                salida.append(limpio)
            elif isinstance(item, str):
                proxy = _desde_url(item)
                if proxy:
                    salida.append(proxy)
        return salida
    salida = []
    for trozo in crudo.replace("\n", ",").split(","):
        proxy = _desde_url(trozo)
        if proxy:
            salida.append(proxy)
    return salida


class PoolProxies:
    def __init__(self, bruto: str | None = None) -> None:
        self._lock = threading.Lock()
        self._proxies = parsear(bruto if bruto is not None else os.environ.get("PROXY_POOL", ""))
        self._castigados: dict[str, float] = {}
        self._turno = 0
        self.estrategia = (os.environ.get("PROXY_ESTRATEGIA", "round_robin") or "").strip().lower()
        self._pegajoso: dict[str, dict[str, str]] = {}

    def __len__(self) -> int:
        return len(self._proxies)

    def disponibles(self) -> list[dict[str, str]]:
        ahora = time.monotonic()
        with self._lock:
            return [p for p in self._proxies if self._castigados.get(p["server"], 0) <= ahora]

    def elegir(self, host: str = "") -> dict[str, str] | None:
        libres = self.disponibles() or self._proxies
        if not libres:
            return None
        if self.estrategia == "sticky" and host:
            with self._lock:
                fijo = self._pegajoso.get(host)
                if fijo and fijo in libres:
                    return fijo
                elegido = random.choice(libres)
                self._pegajoso[host] = elegido
                return elegido
        with self._lock:
            self._turno = (self._turno + 1) % len(libres)
            return libres[self._turno]

    def castigar(self, proxy: dict[str, str] | None, segundos: float = CASTIGO_SEG) -> None:
        if not proxy:
            return
        with self._lock:
            self._castigados[proxy["server"]] = time.monotonic() + max(5.0, segundos)

    def describir(self) -> str:
        return f"{len(self._proxies)} proxies ({len(self.disponibles())} libres)"
<<<ENDFILE>>>
<<<FILE:perimetro/waf.py>>>
# perimetro/waf.py
# Distingue OK de challenge/bloqueo real: un 200 con interstitial NO es éxito.
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_COOLDOWN = Path(os.environ.get("PERIMETRO_COOLDOWN", "") or RAIZ / "perimetro_cooldown.json")
COOLDOWN_MIN = 45

try:
    from core.source_resilience import BLOCKING_STATES, classify_status  # type: ignore
except ImportError:  # pragma: no cover
    BLOCKING_STATES = {401, 403, 407, 429, 451, 500, 502, 503, 504, 520, 521, 522, 523, 524}

    def classify_status(codigo: int) -> str:  # type: ignore
        if codigo == 200:
            return "ok"
        if codigo in (401, 407):
            return "unauthorized"
        if codigo == 403:
            return "forbidden"
        if codigo == 429:
            return "rate_limited"
        if codigo == 404:
            return "not_found"
        if codigo >= 500:
            return "server_error"
        if 300 <= codigo < 400:
            return "redirect"
        return f"http_{codigo}"

BLOQUEOS = {"forbidden", "rate_limited", "blocked_waf", "captcha_required", "unauthorized", "server_error"}

FIRMAS = (
    ("captcha_required", ("g-recaptcha", "h-captcha", "hcaptcha", "cf-turnstile", "funcaptcha", "px-captcha", "captcha-delivery")),
    ("blocked_waf", ("just a moment", "checking your browser", "enable javascript and cookies", "attention required", "access denied", "request unsuccessful. incapsula", "datadome", "_incapsula_resource", "px-captcha", "sec-if-cpt-container")),
    ("rate_limited", ("too many requests", "rate limit", "you have exceeded", "slow down")),
)

CABECERAS_WAF = {
    "blocked_waf": ("cf-mitigated", "x-datadome", "x-akamai-bm", "x-kpsdk-ct", "x-iinfo"),
    "captcha_required": ("x-datadome-captcha", "x-px-captcha"),
}

MARCAS_WAF = {
    "cloudflare": ("cf-ray", "cf-cache-status", "__cf_bm", "cloudflare"),
    "akamai": ("_abck", "akamai", "x-akamai"),
    "datadome": ("datadome", "x-datadome"),
    "perimeterx": ("_px", "px-captcha", "perimeterx"),
    "incapsula": ("visid_incap", "incap_ses", "_incapsula_resource"),
    "kasada": ("x-kpsdk", "kasada"),
    "imperva": ("x-iinfo", "imperva"),
}


def _muestra(cuerpo: str) -> str:
    return (cuerpo or "")[:40000].lower()


def clasificar(codigo: int, cabeceras: dict[str, str] | None = None, cuerpo: str = "") -> str:
    base = classify_status(codigo)
    if base == "ok":
        muestra = _muestra(cuerpo)
        bajo = {str(k).lower(): str(v) for k, v in (cabeceras or {}).items()}
        for estado, firmas in FIRMAS:
            if any(firma in muestra for firma in firmas):
                return estado
        for estado, claves in CABECERAS_WAF.items():
            if any(clave in bajo for clave in claves):
                if estado == "blocked_waf" and bajo.get("x-datadome") and "geo.captcha-delivery" in muestra:
                    return "captcha_required"
                if estado == "blocked_waf":
                    return estado
        if len(cuerpo or "") < 1200 and re.search(r"challenge|verific|robot", muestra):
            return "blocked_waf"
        return "ok"
    if base in ("forbidden", "unauthorized") and cuerpo:
        muestra = _muestra(cuerpo)
        if any(f in muestra for f in ("captcha", "turnstile", "just a moment")):
            return "captcha_required"
        return "blocked_waf"
    return base


def waf_de(cabeceras: dict[str, str] | None, cuerpo: str = "") -> str:
    bajo = {str(k).lower(): str(v) for k, v in (cabeceras or {}).items()}
    muestra = _muestra(cuerpo)
    for proveedor, pistas in MARCAS_WAF.items():
        if any(p in bajo or p in muestra for p in pistas):
            return proveedor
    return ""


class Cooldown:
    def __init__(self, ruta: Path | None = None, minutos: int | None = None) -> None:
        self.ruta = Path(ruta or RUTA_COOLDOWN)
        self.minutos = minutos if minutos is not None else _minutos()

    def _leer(self) -> dict[str, dict]:
        try:
            data = json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _escribir(self, data: dict[str, dict]) -> None:
        try:
            self.ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def activo(self, host: str) -> dict | None:
        entrada = self._leer().get(host)
        if not entrada:
            return None
        if float(entrada.get("hasta", 0)) <= time.time():
            return None
        return entrada

    def registrar(self, host: str, motivo: str, waf: str = "") -> None:
        if not host:
            return
        data = self._leer()
        data[host] = {"motivo": motivo, "waf": waf, "hasta": time.time() + self.minutos * 60, "iso": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._escribir(data)

    def limpiar(self, host: str) -> None:
        data = self._leer()
        if data.pop(host, None) is not None:
            self._escribir(data)

    def listar(self) -> dict[str, dict]:
        ahora = time.time()
        return {h: v for h, v in self._leer().items() if float(v.get("hasta", 0)) > ahora}


def _minutos() -> int:
    try:
        return max(1, int(os.environ.get("PERIMETRO_COOLDOWN_MIN", "") or COOLDOWN_MIN))
    except (TypeError, ValueError):
        return COOLDOWN_MIN
<<<ENDFILE>>>
<<<FILE:perimetro/captcha.py>>>
# perimetro/captcha.py
# Detección de CAPTCHA y resolución opcional vía proveedor externo.
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

TIPOS = {
    "recaptcha": ("g-recaptcha", "grecaptcha", "recaptcha/api.js", "data-sitekey"),
    "hcaptcha": ("h-captcha", "hcaptcha.com", "data-hcaptcha"),
    "turnstile": ("cf-turnstile", "challenges.cloudflare.com/turnstile"),
    "funcaptcha": ("funcaptcha", "arkoselabs", "enforcement"),
}

SITEKEY = re.compile(r"data-sitekey=[\"']([A-Za-z0-9_\-]{10,})[\"']", re.I)


def detectar(cuerpo: str) -> str:
    muestra = (cuerpo or "")[:60000].lower()
    for tipo, pistas in TIPOS.items():
        if any(pista in muestra for pista in pistas):
            return tipo
    return ""


def sitekey(cuerpo: str) -> str:
    encontrado = SITEKEY.search(cuerpo or "")
    return encontrado.group(1) if encontrado else ""


def configurado() -> bool:
    return bool(os.environ.get("CAPTCHA_PROVIDER", "").strip() and os.environ.get("CAPTCHA_API_KEY", "").strip())


def _post(url: str, datos: dict) -> dict:
    cuerpo = urllib.parse.urlencode(datos).encode()
    peticion = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(peticion, timeout=30) as respuesta:
        return json.loads(respuesta.read().decode("utf-8", "replace") or "{}")


def _crear(proveedor: str, clave: str, tipo: str, pagina: str, llave: str) -> str:
    if proveedor == "2captcha":
        datos = {"key": clave, "method": "userrecaptcha", "googlekey": llave, "pageurl": pagina, "json": 1}
        if tipo == "hcaptcha":
            datos = {"key": clave, "method": "hcaptcha", "sitekey": llave, "pageurl": pagina, "json": 1}
        elif tipo == "turnstile":
            datos = {"key": clave, "method": "turnstile", "sitekey": llave, "pageurl": pagina, "json": 1}
        return str(_post("https://2captcha.com/in.php", datos).get("request", ""))
    if proveedor in ("capsolver", "capmonster"):
        tipo_cap = {"recaptcha": "ReCaptchaV2TaskProxyLess", "hcaptcha": "HCaptchaTaskProxyLess", "turnstile": "AntiTurnstileTaskProxyLess"}.get(tipo, "ReCaptchaV2TaskProxyLess")
        base = "https://api.capsolver.com" if proveedor == "capsolver" else "https://api.capmonster.cloud"
        cuerpo = {"clientKey": clave, "task": {"type": tipo_cap, "websiteURL": pagina, "websiteKey": llave}}
        peticion = urllib.request.Request(base + "/createTask", data=json.dumps(cuerpo).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(peticion, timeout=30) as respuesta:
            return str(json.loads(respuesta.read().decode("utf-8", "replace") or "{}").get("taskId", ""))
    return ""


def _resultado(proveedor: str, clave: str, tarea: str) -> str:
    if not tarea:
        return ""
    for _ in range(28):
        time.sleep(6)
        try:
            if proveedor == "2captcha":
                token = _post("https://2captcha.com/res.php", {"key": clave, "action": "get", "id": tarea, "json": 1})
                if token.get("status") == 1:
                    return str(token.get("request", ""))
            else:
                base = "https://api.capsolver.com" if proveedor == "capsolver" else "https://api.capmonster.cloud"
                peticion = urllib.request.Request(base + "/getTaskResult", data=json.dumps({"clientKey": clave, "taskId": tarea}).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(peticion, timeout=30) as respuesta:
                    data = json.loads(respuesta.read().decode("utf-8", "replace") or "{}")
                if data.get("status") == "ready":
                    solucion = data.get("solution") or {}
                    return str(solucion.get("gRecaptchaResponse") or solucion.get("token") or "")
        except Exception:
            continue
    return ""


def resolver(cuerpo: str, pagina: str) -> dict[str, str]:
    if not configurado():
        return {}
    tipo = detectar(cuerpo)
    llave = sitekey(cuerpo)
    if not tipo or not llave:
        return {}
    proveedor = os.environ["CAPTCHA_PROVIDER"].strip().lower()
    clave = os.environ["CAPTCHA_API_KEY"].strip()
    try:
        token = _resultado(proveedor, clave, _crear(proveedor, clave, tipo, pagina, llave))
    except Exception:
        return {}
    return {"tipo": tipo, "token": token} if token else {}
<<<ENDFILE>>>
<<<FILE:perimetro/stealth.py>>>
# perimetro/stealth.py
from __future__ import annotations

import os
import random

NAVEGADORES = ("chrome", "chrome131", "chrome124", "edge101", "safari17_0")
ACEPTAR = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"


def _si(nombre: str, defecto: bool = False) -> bool:
    crudo = os.environ.get(nombre)
    if crudo is None:
        return defecto
    return crudo.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def impersonate() -> str:
    return (os.environ.get("PERIMETRO_IMPERSONATE", "") or random.choice(NAVEGADORES)).strip()


def headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": ACEPTAR,
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        base.update({str(k): str(v) for k, v in extra.items() if v})
    return base


def kwargs_http() -> dict:
    return {"stealthy_headers": True, "impersonate": impersonate(), "http3": _si("PERIMETRO_HTTP3", True), "follow_redirects": True, "max_redirects": 8, "headers": headers()}


def kwargs_stealth(ms: int = 45000, espera_selector: str = "") -> dict:
    opciones = {
        "headless": _si("PERIMETRO_HEADLESS", True),
        "humanize": _si("PERIMETRO_HUMANIZE", True),
        "geoip": _si("PERIMETRO_GEOIP", True),
        "block_webrtc": True,
        "allow_webgl": _si("PERIMETRO_WEBGL", True),
        "hide_canvas": _si("PERIMETRO_HIDE_CANVAS", True),
        "network_idle": _si("PERIMETRO_NETWORK_IDLE", True),
        "timeout": ms,
        "wait": int(os.environ.get("PERIMETRO_WAIT_MS", "1200") or 1200),
        "real_challenge": _si("PERIMETRO_REAL_CHALLENGE", True),
        "solve_cloudflare": _si("PERIMETRO_SOLVE_CLOUDFLARE", False),
        "extra_headers": headers(),
        "disable_resources": _si("PERIMETRO_BLOQUEAR_RECURSOS", False),
    }
    if espera_selector:
        opciones["wait_selector"] = espera_selector
        opciones["wait_selector_state"] = "attached"
    return opciones


SCRIPT_EVASION = """
(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['es-MX', 'es', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  window.chrome = window.chrome || { runtime: {} };
  if (!window.Notification) { window.Notification = { permission: 'default' }; }
})();
"""


def humano() -> float:
    return random.uniform(0.6, 2.2)
<<<ENDFILE>>>
<<<FILE:perimetro/transporte.py>>>
# perimetro/transporte.py
# Único punto de salida a red. Orden: http -> stealth -> browser.
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from . import captcha, stealth, waf
from .proxies import PoolProxies
from .ritmo import Ritmo

try:
    from core.source_resilience import classify_status  # type: ignore
except ImportError:  # pragma: no cover
    from .waf import classify_status  # type: ignore

ESTADOS_REINTENTO = {"timeout", "server_error", "network_error", "rate_limited", "redirect"}
ESTADOS_CORTE = {"blocked_waf", "captcha_required", "forbidden", "unauthorized", "not_found"}

try:
    from scrapling.fetchers import Fetcher as _Fetcher  # type: ignore
except Exception:  # pragma: no cover
    _Fetcher = None
try:
    from scrapling.fetchers import StealthyFetcher as _StealthyFetcher  # type: ignore
except Exception:  # pragma: no cover
    _StealthyFetcher = None
try:
    from patchright.sync_api import sync_playwright as _patchright  # type: ignore
except Exception:  # pragma: no cover
    _patchright = None


def disponibles() -> dict[str, bool]:
    return {"scrapling_http": _Fetcher is not None, "scrapling_stealth": _StealthyFetcher is not None, "patchright": _patchright is not None}


@dataclass
class Respuesta:
    ok: bool
    estado: str
    http: int
    texto: str = ""
    url_final: str = ""
    motor: str = ""
    waf: str = ""
    ms: int = 0
    intentos: int = 0
    detalle: str = ""
    cabeceras: dict[str, str] = field(default_factory=dict)

    @property
    def bloqueada(self) -> bool:
        return self.estado in ESTADOS_CORTE


def _texto(respuesta: Any) -> str:
    for atributo in ("body", "html_content", "text"):
        valor = getattr(respuesta, atributo, None)
        if isinstance(valor, bytes):
            return valor.decode("utf-8", "replace")
        if isinstance(valor, str):
            return valor
    return str(respuesta or "")


def _cabeceras(respuesta: Any) -> dict[str, str]:
    try:
        return {str(k): str(v) for k, v in dict(getattr(respuesta, "headers", None) or {}).items()}
    except Exception:
        return {}


class Transporte:
    def __init__(self, proxies: PoolProxies | None = None, ritmo: Ritmo | None = None) -> None:
        self.proxies = proxies or PoolProxies()
        self.ritmo = ritmo or Ritmo()
        self.cooldown = waf.Cooldown()
        self.intentos_max = int(os.environ.get("PERIMETRO_INTENTOS", "2") or 2)
        self.motor = (os.environ.get("PERIMETRO_MOTOR", "auto") or "auto").strip().lower()

    def get(self, url: str, *, motor: str = "http", referer: str = "", espera_selector: str = "", forzar_cooldown: bool = False, extra: dict[str, str] | None = None) -> Respuesta:
        host = url.split("//", 1)[-1].split("/", 1)[0]
        enfriado = self.cooldown.activo(host)
        if enfriado and not forzar_cooldown:
            return Respuesta(False, "cooldown", 0, detalle=f"host en enfriamiento por {enfriado.get('motivo')}")

        orden = self._orden(motor)
        if not orden:
            return Respuesta(False, "sin_navegador", 0, detalle="ningún motor disponible")

        ultima = Respuesta(False, "sin_intento", 0)
        for intento in range(self.intentos_max + 1):
            for candidato in orden:
                respuesta = self._ejecutar(candidato, url, referer, espera_selector, extra)
                respuesta.intentos = intento + 1
                ultima = respuesta
                if respuesta.ok:
                    self.cooldown.limpiar(host)
                    return respuesta
                if respuesta.estado in ESTADOS_CORTE:
                    if respuesta.estado in ("blocked_waf", "captcha_required"):
                        self.cooldown.registrar(host, respuesta.estado, respuesta.waf)
                    return respuesta
            if intento < self.intentos_max:
                time.sleep(self.ritmo.backoff(intento))
        return ultima

    def _orden(self, motor: str) -> list[str]:
        pedido = self.motor if self.motor != "auto" else motor
        if pedido == "http":
            return ["http_scrapling", "http_requests"] if _Fetcher else ["http_requests"]
        if pedido == "stealth":
            cola = ["stealth"] if _StealthyFetcher else []
            return cola + (["browser"] if _patchright else []) + ["http_requests"]
        if pedido == "browser":
            return (["browser"] if _patchright else []) + ["http_requests"]
        return ["http_requests"]

    def _proxy(self, host: str) -> dict[str, str] | None:
        return self.proxies.elegir(host)

    def _ejecutar(self, candidato: str, url: str, referer: str, espera_selector: str, extra: dict | None) -> Respuesta:
        host = url.split("//", 1)[-1].split("/", 1)[0]
        self.ritmo.esperar(host)
        proxy = self._proxy(host)
        inicio = time.monotonic()
        try:
            if candidato == "http_scrapling":
                crudo = _Fetcher.get(url, timeout=30, retries=1, proxy=proxy, **_limpiar(stealth.kwargs_http(), url, referer, extra))
            elif candidato == "http_requests":
                crudo = requests.get(url, headers=stealth.headers({"Referer": referer} if referer else extra), timeout=25, proxies=({"http": proxy["server"], "https": proxy["server"]} if proxy and not proxy.get("username") else None))
            elif candidato == "stealth":
                crudo = _StealthyFetcher.fetch(url, proxy=proxy, **stealth.kwargs_stealth(espera_selector=espera_selector))
            elif candidato == "browser":
                crudo = self._navegador(url, proxy, espera_selector)
            else:
                return Respuesta(False, "sin_navegador", 0)
        except requests.Timeout:
            return Respuesta(False, "timeout", 0, motor=candidato, ms=_ms(inicio))
        except Exception as exc:
            nombre = type(exc).__name__
            estado = "timeout" if "timeout" in nombre.lower() else "network_error"
            if proxy:
                self.proxies.castigar(proxy)
            return Respuesta(False, estado, 0, motor=candidato, ms=_ms(inicio), detalle=nombre)

        codigo = int(getattr(crudo, "status", getattr(crudo, "status_code", 0)) or 0)
        texto = _texto(crudo)
        cabeceras = _cabeceras(crudo)
        estado = waf.clasificar(codigo, cabeceras, texto)
        proveedor = waf.waf_de(cabeceras, texto)

        if estado == "captcha_required" and captcha.configurado():
            resuelto = captcha.resolver(texto, url)
            if resuelto.get("token"):
                estado = "captcha_resuelto"

        if estado == "rate_limited":
            self.ritmo.penalizar(host, 20.0)
        if proxy and estado in ESTADOS_CORTE:
            self.proxies.castigar(proxy, 60.0)

        return Respuesta(ok=estado in ("ok", "captcha_resuelto"), estado="ok" if estado == "captcha_resuelto" else estado, http=codigo, texto=texto, url_final=str(getattr(crudo, "url", url) or url), motor=candidato, waf=proveedor, ms=_ms(inicio), cabeceras=cabeceras)

    def _navegador(self, url: str, proxy: dict | None, espera_selector: str) -> Any:
        with _patchright() as pw:
            lanzamiento: dict[str, Any] = {"headless": True}
            if proxy:
                lanzamiento["proxy"] = proxy
            navegador = pw.chromium.launch(**lanzamiento)
            contexto = navegador.new_context(locale="es-MX", timezone_id="America/Mexico_City", viewport={"width": 1440, "height": 900}, user_agent=stealth.headers()["User-Agent"])
            contexto.add_init_script(stealth.SCRIPT_EVASION)
            pagina = contexto.new_page()
            pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
            if espera_selector:
                try:
                    pagina.wait_for_selector(espera_selector, timeout=15000)
                except Exception:
                    pass
            time.sleep(stealth.humano())
            html = pagina.content()
            navegador.close()
            return _Crudo(200, html, {}, url)


class _Crudo:
    def __init__(self, status: int, texto: str, cabeceras: dict, url: str) -> None:
        self.status = status
        self.text = texto
        self.headers = cabeceras
        self.url = url


def _ms(inicio: float) -> int:
    return int((time.monotonic() - inicio) * 1000)


def _limpiar(opciones: dict, url: str, referer: str, extra: dict | None) -> dict:
    salida = {k: v for k, v in opciones.items() if v not in (None, "", False)}
    cabeceras = dict(salida.pop("headers", {}) or {})
    if referer:
        cabeceras["Referer"] = referer
    if extra:
        cabeceras.update({str(k): str(v) for k, v in extra.items()})
    if cabeceras:
        salida["headers"] = cabeceras
    return salida
<<<ENDFILE>>>
<<<FILE:perimetro/extraccion.py>>>
# perimetro/extraccion.py
# Extractores por estrategia. Estructurado primero, selectores al final.
from __future__ import annotations

import html as _html
import json
import re
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

try:
    from core.price_sanitizer import parse_price, sanear_par_precios  # type: ignore
except ImportError:  # pragma: no cover
    def parse_price(valor: Any) -> float | None:  # type: ignore
        if valor is None or isinstance(valor, bool):
            return None
        if isinstance(valor, (int, float)):
            return float(valor) if 0 < float(valor) < 2_000_000 else None
        limpio = re.sub(r"[^\d.,]", "", str(valor))
        if not limpio:
            return None
        if "," in limpio and "." in limpio:
            limpio = limpio.replace(",", "") if limpio.rfind(".") > limpio.rfind(",") else limpio.replace(".", "").replace(",", ".")
        elif "," in limpio:
            limpio = limpio.replace(",", ".") if len(limpio.split(",")[-1]) == 2 else limpio.replace(",", "")
        try:
            numero = float(limpio)
        except ValueError:
            return None
        return numero if 0 < numero < 2_000_000 else None

    def sanear_par_precios(actual, anterior, _texto=""):  # type: ignore
        return actual, anterior, ""

PRECIO = re.compile(r"\$\s*([\d][\d.,]{1,14})")
DESCUENTO = re.compile(r"(\d{1,2})\s*%\s*(?:de\s*)?(?:descuento|off|menos)", re.I)
TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>", re.S)
ATRIBUTO = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")


def _attrs(bruto: str) -> dict[str, str]:
    salida = {}
    for encontrado in ATRIBUTO.finditer(bruto or ""):
        salida[encontrado.group(1).lower()] = _html.unescape(encontrado.group(2).strip("\"'"))
    return salida


def _texto_html(trozo: str) -> str:
    limpio = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", trozo, flags=re.S | re.I)
    limpio = re.sub(r"<[^>]+>", " ", limpio)
    return re.sub(r"\s+", " ", _html.unescape(limpio)).strip()


def _absoluta(base: str, enlace: str) -> str:
    if not enlace:
        return ""
    if enlace.startswith("//"):
        return "https:" + enlace
    if enlace.startswith("http"):
        return enlace
    return urljoin(base, enlace)


def _precios(texto: str) -> list[float]:
    salida = []
    for encontrado in PRECIO.finditer(texto or ""):
        valor = parse_price(encontrado.group(1))
        if valor:
            salida.append(valor)
    return salida


def _par(actual: Any, anterior: Any, texto: str = "") -> tuple[float | None, float | None, int]:
    precio, referencia, _ = sanear_par_precios(parse_price(actual), parse_price(anterior), texto)
    descuento = 0
    if precio and referencia and referencia > precio:
        descuento = round((1 - precio / referencia) * 100)
    else:
        hallado = DESCUENTO.search(texto or "")
        if hallado:
            descuento = int(hallado.group(1))
    return precio, referencia, descuento


def _fila(tienda: str, titulo: str, url: str, precio: float | None, referencia: float | None, descuento: int, origen: str, ident: str = "", imagen: str = "") -> dict | None:
    if not titulo or not url or not precio:
        return None
    return {"tienda": tienda, "titulo": re.sub(r"\s+", " ", titulo).strip()[:300], "url": url, "precio_actual": precio, "precio_anterior": referencia if referencia and referencia > precio else None, "descuento": max(0, min(99, descuento)), "origen_link": origen, "tipo_fuente": "ONLINE", "id": ident or None, "imagen": imagen or None}


def _tokenizar(regla: str) -> tuple[str, list[str], str, dict[str, str]]:
    tag = ""
    clases: list[str] = []
    ident = ""
    attrs: dict[str, str] = {}
    resto = regla.strip()
    encontrado = re.match(r"^([a-zA-Z][a-zA-Z0-9-]*)", resto)
    if encontrado:
        tag = encontrado.group(1).lower()
        resto = resto[encontrado.end():]
    for clase in re.findall(r"\.([A-Za-z0-9_-]+)", resto):
        clases.append(clase)
    ident_encontrado = re.search(r"#([A-Za-z0-9_-]+)", resto)
    if ident_encontrado:
        ident = ident_encontrado.group(1)
    for attr in re.finditer(r"\[\s*([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*(?:=\s*(\"[^\"]*\"|'[^']*'|[^\]]*))?\]", resto):
        attrs[attr.group(1).lower()] = (attr.group(2) or "").strip("\"'")
    return tag, clases, ident, attrs


def _coincide(attrs: dict[str, str], tag: str, clases: list[str], ident: str, filtros: dict[str, str]) -> bool:
    if tag and tag != "*" and tag != attrs.get("__tag"):
        return False
    if ident and attrs.get("id") != ident:
        return False
    if clases and not set(clases).issubset(set((attrs.get("class") or "").split())):
        return False
    for clave, valor in filtros.items():
        if clave not in attrs or (valor and valor != attrs.get(clave)):
            return False
    return True


def nodos(cuerpo: str, regla: str) -> list[dict[str, Any]]:
    if not cuerpo or not regla:
        return []
    partes = [p for p in regla.split() if p]
    tag, clases, ident, filtros = _tokenizar(partes[-1])
    salida: list[dict[str, Any]] = []
    for encontrado in TAG.finditer(cuerpo):
        if encontrado.group(1) == "/":
            continue
        attrs = _attrs(encontrado.group(3))
        attrs["__tag"] = encontrado.group(2).lower()
        if not _coincide(attrs, tag, clases, ident, filtros):
            continue
        inicio = encontrado.end()
        if partes[:-1] and not _tiene_ancestro(cuerpo, encontrado.start(), partes[:-1]):
            continue
        salida.append({"attrs": attrs, "html": cuerpo[inicio:_cierre(cuerpo, inicio, encontrado.group(2))]})
    return salida


def _cierre(cuerpo: str, inicio: int, etiqueta: str) -> int:
    patron = re.compile(rf"</?{re.escape(etiqueta)}\b", re.I)
    nivel = 1
    for encontrado in patron.finditer(cuerpo, inicio):
        nivel += -1 if cuerpo[encontrado.start() + 1] == "/" else 1
        if nivel == 0:
            return encontrado.start()
    return min(len(cuerpo), inicio + 4000)


def _tiene_ancestro(cuerpo: str, posicion: int, partes: list[str]) -> bool:
    previo = cuerpo[max(0, posicion - 4000):posicion]
    for parte in partes:
        tag, clases, ident, filtros = _tokenizar(parte)
        hallado = False
        for encontrado in TAG.finditer(previo):
            if encontrado.group(1) == "/":
                continue
            attrs = _attrs(encontrado.group(3))
            attrs["__tag"] = encontrado.group(2).lower()
            if _coincide(attrs, tag, clases, ident, filtros):
                hallado = True
                break
        if not hallado:
            return False
    return True


def vtex(payload: Any, tienda: str, base: str) -> list[dict]:
    filas = []
    for producto in payload if isinstance(payload, list) else []:
        if not isinstance(producto, dict):
            continue
        mejor = None
        for sku in producto.get("items") or []:
            for vendedor in (sku or {}).get("sellers") or []:
                oferta = (vendedor or {}).get("commertialOffer") or {}
                if (parse_price(oferta.get("AvailableQuantity")) or 0) <= 0:
                    continue
                precio = parse_price(oferta.get("Price"))
                if not precio:
                    continue
                referencia = None
                for campo in ("ListPrice", "PriceWithoutDiscount"):
                    candidato = parse_price(oferta.get(campo))
                    if candidato and candidato > precio:
                        referencia = candidato
                        break
                ranking = ((1 - precio / referencia) if referencia else 0, -precio)
                if mejor is None or ranking > mejor[0]:
                    mejor = (ranking, precio, referencia)
        if not mejor:
            continue
        precio, referencia, descuento = _par(mejor[1], mejor[2])
        fila = _fila(tienda, str(producto.get("productName") or ""), _absoluta(base, str(producto.get("link") or "")), precio, referencia, descuento, "vtex_catalogo_publico", str(producto.get("productId") or ""))
        if fila:
            filas.append(fila)
    return filas


def sfcc(payload: Any, tienda: str, base: str) -> list[dict]:
    filas = []
    grupos: list[Any] = []
    if isinstance(payload, dict):
        for clave in ("suggestions", "products", "productSuggestions"):
            valor = payload.get(clave)
            if isinstance(valor, list):
                grupos.extend(valor)
            elif isinstance(valor, dict):
                for interno in valor.values():
                    if isinstance(interno, list):
                        grupos.extend(interno)
    for item in grupos:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("name") or item.get("productName") or item.get("title") or "")
        enlace = str(item.get("url") or item.get("link") or item.get("productUrl") or "")
        precio, referencia, descuento = _par(item.get("price") or item.get("salePrice"), item.get("listPrice") or item.get("standardPrice"), nombre)
        fila = _fila(tienda, nombre, _absoluta(base, enlace), precio, referencia, descuento, "sfcc_sugerencias", str(item.get("id") or ""))
        if fila:
            filas.append(fila)
    return filas


def _recorrer(nodo: Any, salida: list[dict]) -> None:
    if isinstance(nodo, dict):
        precio = None
        for clave in ("currentPrice", "salePrice", "price", "finalPrice", "sellingPrice"):
            if clave in nodo:
                precio = nodo.get(clave)
                break
        nombre = nodo.get("name") or nodo.get("title") or nodo.get("productName") or nodo.get("displayName")
        enlace = nodo.get("url") or nodo.get("link") or nodo.get("productUrl") or nodo.get("canonicalUrl")
        if isinstance(precio, dict):
            precio = precio.get("value") or precio.get("amount") or precio.get("current")
        if precio is not None and nombre and enlace and isinstance(precio, (int, float, str)):
            salida.append({"nombre": str(nombre), "url": str(enlace), "precio": precio, "antes": nodo.get("listPrice") or nodo.get("strikethroughPrice") or nodo.get("wasPrice"), "id": nodo.get("id") or nodo.get("sku") or nodo.get("usItemId")})
        for valor in nodo.values():
            _recorrer(valor, salida)
    elif isinstance(nodo, list):
        for valor in nodo:
            _recorrer(valor, salida)


def nextdata(cuerpo: str, tienda: str, base: str) -> list[dict]:
    encontrados = []
    for patron in (r"<script[^>]+id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", r"window\.__NUXT__\s*=\s*(\{.*?\});", r"__APOLLO_STATE__\s*=\s*(\{.*?\});"):
        for hallado in re.finditer(patron, cuerpo or "", re.S):
            try:
                data = json.loads(hallado.group(1))
            except ValueError:
                continue
            crudos: list[dict] = []
            _recorrer(data, crudos)
            for crudo in crudos:
                precio, referencia, descuento = _par(crudo["precio"], crudo.get("antes"))
                fila = _fila(tienda, crudo["nombre"], _absoluta(base, crudo["url"]), precio, referencia, descuento, "next_data", str(crudo.get("id") or ""))
                if fila:
                    encontrados.append(fila)
    return encontrados


def jsonld(cuerpo: str, tienda: str, base: str) -> list[dict]:
    filas = []
    for hallado in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", cuerpo or "", re.S):
        try:
            data = json.loads(_html.unescape(hallado.group(1)).strip())
        except ValueError:
            continue
        for bloque in data if isinstance(data, list) else [data]:
            if not isinstance(bloque, dict):
                continue
            tipo = str(bloque.get("@type") or "")
            if "ItemList" in tipo:
                for elemento in bloque.get("itemListElement") or []:
                    if isinstance(elemento, dict):
                        item = elemento.get("item") or elemento
                        if isinstance(item, dict):
                            filas.extend(_de_producto(item, tienda, base))
            elif "Product" in tipo:
                filas.extend(_de_producto(bloque, tienda, base))
    return filas


def _de_producto(producto: dict, tienda: str, base: str) -> list[dict]:
    ofertas = producto.get("offers") or {}
    if isinstance(ofertas, list):
        ofertas = ofertas[0] if ofertas else {}
    if not isinstance(ofertas, dict):
        ofertas = {}
    precio, referencia, descuento = _par(ofertas.get("price") or ofertas.get("lowPrice"), producto.get("highPrice"))
    fila = _fila(tienda, str(producto.get("name") or ""), _absoluta(base, str(producto.get("url") or ofertas.get("url") or "")), precio, referencia, descuento, "jsonld", str(producto.get("sku") or producto.get("productID") or ""), str(producto.get("image") or ""))
    return [fila] if fila else []


def amazon(cuerpo: str, tienda: str, base: str, selectores: dict[str, str]) -> list[dict]:
    filas = []
    for tarjeta in nodos(cuerpo, selectores.get("card", "div[data-component-type=\"s-search-result\"]")):
        attrs = tarjeta["attrs"]
        enlace = ""
        for ancla in nodos(tarjeta["html"], selectores.get("link", "h2 a")):
            enlace = ancla["attrs"].get("href", "")
            if enlace:
                break
        titulo = ""
        for nodo_titulo in nodos(tarjeta["html"], selectores.get("title", "h2 span")):
            titulo = _texto_html(nodo_titulo["html"])
            if titulo:
                break
        texto = _texto_html(tarjeta["html"])
        precios = _precios(texto)
        antes = None
        for nodo_antes in nodos(tarjeta["html"], selectores.get("antes", "span.a-text-price span.a-offscreen")):
            valor = parse_price(_texto_html(nodo_antes["html"]))
            if valor:
                antes = valor
                break
        precio, referencia, descuento = _par(min(precios) if precios else None, antes, texto)
        fila = _fila(tienda, titulo, _absoluta(base, enlace), precio, referencia, descuento, "html_selectores", str(attrs.get("data-asin") or ""))
        if fila:
            filas.append(fila)
    return filas


def selectores(cuerpo: str, tienda: str, base: str, sel: dict[str, str]) -> list[dict]:
    filas = []
    if not sel.get("card"):
        return filas
    for tarjeta in nodos(cuerpo, sel["card"]):
        enlace = ""
        for ancla in nodos(tarjeta["html"], sel.get("link", "a[href]")):
            enlace = ancla["attrs"].get("href", "")
            if enlace:
                break
        if not enlace:
            enlace = tarjeta["attrs"].get("href", "")
        titulo = ""
        for nodo_titulo in nodos(tarjeta["html"], sel.get("title", "a")):
            titulo = _texto_html(nodo_titulo["html"]) or nodo_titulo["attrs"].get("title", "")
            if titulo:
                break
        if not titulo:
            titulo = tarjeta["attrs"].get("title", "")
        texto = _texto_html(tarjeta["html"])
        antes = None
        if sel.get("antes"):
            for nodo_antes in nodos(tarjeta["html"], sel["antes"]):
                valor = parse_price(_texto_html(nodo_antes["html"]))
                if valor:
                    antes = valor
                    break
        precio = None
        if sel.get("price"):
            for nodo_precio in nodos(tarjeta["html"], sel["price"]):
                valor = parse_price(_texto_html(nodo_precio["html"]))
                if valor:
                    precio = valor
                    break
        if precio is None:
            precios = _precios(texto)
            precio = min(precios) if precios else None
        precio, referencia, descuento = _par(precio, antes, texto)
        fila = _fila(tienda, titulo, _absoluta(base, enlace), precio, referencia, descuento, "html_selectores")
        if fila:
            filas.append(fila)
    return filas


def deduplicar(filas: Iterable[dict]) -> list[dict]:
    vistos = set()
    salida = []
    for fila in filas:
        clave = (fila.get("id") or "", urlparse(fila.get("url") or "").path)
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(fila)
    return salida
<<<ENDFILE>>>
<<<FILE:perimetro/adaptadores.py>>>
# perimetro/adaptadores.py
from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterable

from . import extraccion
from .registry import Tienda, seleccionar, tiendas_activas
from .transporte import Respuesta, Transporte

try:
    from core.scraper_base import BaseScraper, ScraperContext, run_scrapers_parallel  # type: ignore
except ImportError:  # pragma: no cover
    class ScraperContext:  # type: ignore
        def __init__(self, *a, **k) -> None:
            pass

    class BaseScraper:  # type: ignore
        def __init__(self, contexto: Any = None) -> None:
            self.context = contexto or ScraperContext()

        def normalize(self, **datos: Any) -> dict:
            return dict(datos)

    def run_scrapers_parallel(scrapers: Iterable[Any], max_workers: int | None = None) -> list[dict]:  # type: ignore
        salida: list[dict] = []
        for scraper in scrapers:
            salida.extend(scraper.discover() or [])
        return salida

try:
    from core.offer_identity import canonical_store  # type: ignore
except ImportError:  # pragma: no cover
    def canonical_store(valor: Any) -> str:  # type: ignore
        return re.sub(r"[^a-z0-9]+", " ", str(valor or "").lower()).strip()

logger = logging.getLogger("DexterH4ck.Perimetro")

TERMINOS = ("liquidacion", "remate", "outlet", "descuento", "ultimas piezas", "mega rebajas")


def _terminos(ident: str) -> tuple[str, ...]:
    crudo = os.environ.get(f"PERIMETRO_{ident.upper()}_QUERIES", "") or os.environ.get("PERIMETRO_QUERIES", "")
    pedidos = tuple(t.strip() for t in crudo.split(",") if t.strip())
    return pedidos or TERMINOS[:3]


def _entero(nombre: str, defecto: int, minimo: int = 1, maximo: int = 10) -> int:
    try:
        return max(minimo, min(maximo, int(os.environ.get(nombre, "") or defecto)))
    except (TypeError, ValueError):
        return defecto


class AdaptadorTienda(BaseScraper):
    def __init__(self, tienda: Tienda, contexto: ScraperContext | None = None, transporte: Transporte | None = None) -> None:
        super().__init__(contexto)
        self.tienda = tienda
        self.store = tienda.nombre
        self.transporte = transporte or Transporte()
        self.last_status: dict[str, Any] = {"state": "not_run"}
        self.estrategia_ganadora = ""

    def discover(self) -> list[dict[str, Any]]:
        if not self.tienda.busqueda and not self.tienda.vtex_host and not self.tienda.sfcc_site:
            self.last_status = {"state": "not_configured", "detail": "tienda sin búsqueda declarada"}
            return []

        maximo = _entero("PERIMETRO_MAX_POR_TIENDA", 40, 1, 200)
        paginas = _entero("PERIMETRO_MAX_PAGINAS", 1, 1, 5)
        filas: list[dict] = []
        errores: list[str] = []
        for termino in _terminos(self.tienda.id):
            for estrategia in self.tienda.estrategias:
                obtenidas = self._estrategia(estrategia, termino, paginas)
                if obtenidas:
                    self.estrategia_ganadora = self.estrategia_ganadora or estrategia
                    filas.extend(obtenidas)
                elif self.last_status.get("state") in ("cooldown", "blocked_waf", "captcha_required"):
                    errores.append(str(self.last_status.get("state")))
            if len(filas) >= maximo:
                break
            if errores and errores[-1] in ("cooldown", "blocked_waf", "captcha_required"):
                break

        filas = extraccion.deduplicar(filas)[:maximo]
        if filas:
            self.last_status = {"state": "ok", "count": len(filas), "estrategia": self.estrategia_ganadora}
        elif errores:
            self.last_status = {"state": errores[-1], "count": 0}
        else:
            self.last_status = {"state": "empty", "count": 0, "estrategia": self.tienda.estrategias[0]}
        logger.info("[perimetro:%s] %s (%s filas)", self.tienda.id, self.last_status.get("state"), len(filas))
        return filas

    def _estrategia(self, estrategia: str, termino: str, paginas: int) -> list[dict]:
        try:
            if estrategia == "vtex":
                return self._vtex(termino, paginas)
            if estrategia == "sfcc":
                return self._sfcc(termino)
            return self._html(estrategia, termino)
        except Exception as exc:
            self.last_status = {"state": "invalid_response", "detail": type(exc).__name__}
            return []

    def _url(self, termino: str) -> str:
        return self.tienda.url_busqueda(termino) or self.tienda.home

    def _vtex(self, termino: str, paginas: int) -> list[dict]:
        host = self.tienda.vtex_host or self.tienda.home
        endpoint = host.rstrip("/") + "/api/catalog_system/pub/products/search"
        salida: list[dict] = []
        for pagina in range(paginas):
            respuesta = self.transporte.get(endpoint, motor="http", referer=self.tienda.home, extra={"_from": str(pagina * 50), "_to": str(pagina * 50 + 49), "ft": termino})
            self._anotar(respuesta)
            if not respuesta.ok:
                break
            try:
                payload = __import__("json").loads(respuesta.texto or "[]")
            except ValueError:
                break
            if not isinstance(payload, list) or not payload:
                break
            salida.extend(extraccion.vtex(payload, self.tienda.nombre, self.tienda.home))
            if len(payload) < 50:
                break
        return salida

    def _sfcc(self, termino: str) -> list[dict]:
        if not self.tienda.sfcc_site:
            return []
        base = self.tienda.home.rstrip("/")
        sugerencias = f"{base}/on/demandware.store/{self.tienda.sfcc_site}/es_MX/SearchServices-GetSuggestions?q={termino}&limit=20"
        respuesta = self.transporte.get(sugerencias, motor="http", referer=self.tienda.home)
        self._anotar(respuesta)
        if respuesta.ok:
            try:
                filas = extraccion.sfcc(__import__("json").loads(respuesta.texto or "{}"), self.tienda.nombre, self.tienda.home)
            except ValueError:
                filas = []
            if filas:
                return filas
        pagina = self.transporte.get(self._url(termino), motor="http" if not self.tienda.requiere_js else "stealth")
        self._anotar(pagina)
        if not pagina.ok:
            return []
        return extraccion.selectores(pagina.texto, self.tienda.nombre, self.tienda.home, self.tienda.selectores)

    def _html(self, estrategia: str, termino: str) -> list[dict]:
        motor = "stealth" if self.tienda.requiere_js else "http"
        respuesta = self.transporte.get(self._url(termino), motor=motor, referer=self.tienda.home, espera_selector=self.tienda.selectores.get("card", ""))
        self._anotar(respuesta)
        if not respuesta.ok:
            return []
        if estrategia == "nextdata":
            return extraccion.nextdata(respuesta.texto, self.tienda.nombre, self.tienda.home)
        if estrategia == "jsonld":
            return extraccion.jsonld(respuesta.texto, self.tienda.nombre, self.tienda.home)
        if estrategia == "amazon":
            return extraccion.amazon(respuesta.texto, self.tienda.nombre, self.tienda.home, self.tienda.selectores)
        return extraccion.selectores(respuesta.texto, self.tienda.nombre, self.tienda.home, self.tienda.selectores)

    def _anotar(self, respuesta: Respuesta) -> None:
        if respuesta.ok:
            if self.last_status.get("state") in (None, "not_run", "empty"):
                self.last_status = {"state": "ok", "count": 0}
            return
        self.last_status = {"state": respuesta.estado, "http": respuesta.http, "waf": respuesta.waf, "motor": respuesta.motor}


def adaptadores(tiendas: list[Tienda] | None = None, contexto: ScraperContext | None = None) -> list[AdaptadorTienda]:
    transporte = Transporte()
    return [AdaptadorTienda(t, contexto, transporte) for t in (tiendas or tiendas_activas())]


class PerimetroScraper:
    def __init__(self, contexto: ScraperContext | None = None, nombres: list[str] | None = None) -> None:
        self.contexto = contexto
        self.scrapers = adaptadores(seleccionar(nombres), contexto)

    def discover(self) -> list[dict[str, Any]]:
        return run_scrapers_parallel(self.scrapers, max_workers=_entero("PERIMETRO_CONCURRENCIA", 4, 1, 8))

    def estado(self) -> dict[str, Any]:
        return {s.tienda.id: s.last_status for s in self.scrapers}
<<<ENDFILE>>>
<<<FILE:perimetro/cli.py>>>
# perimetro/cli.py
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, transporte as capa_transporte
from .adaptadores import AdaptadorTienda
from .proxies import PoolProxies
from .registry import recargar, seleccionar, tiendas_activas
from .ritmo import Ritmo
from .transporte import Transporte
from .waf import Cooldown


def _transporte() -> Transporte:
    return Transporte(PoolProxies(), Ritmo())


def cmd_doctor(_: argparse.Namespace) -> int:
    print(f"perimetro {__version__}")
    for nombre, activo in capa_transporte.disponibles().items():
        print(f"  {nombre:22} {'OK' if activo else 'AUSENTE'}")
    print(f"  proxies                {PoolProxies().describir()}")
    print(f"  hosts en cooldown      {len(Cooldown().listar())}")
    print("\ntiendas:")
    for t in tiendas_activas():
        print(f"  {t.id:12} {'JS' if t.requiere_js else '--'} {t.prioridad:2} {'/'.join(t.estrategias)}")
    print(f"\ntotal: {len(tiendas_activas())} activas de {len(recargar())}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    objetivo = seleccionar(args.tienda)
    if not objetivo:
        print("tienda no encontrada")
        return 2
    transporte = _transporte()
    from . import extraccion

    for t in objetivo:
        respuesta = transporte.get(t.url_busqueda(args.q) or t.home, motor="stealth" if t.requiere_js else "http", forzar_cooldown=args.forzar)
        print(f"\n[{t.id}] {respuesta.estado} http={respuesta.http} motor={respuesta.motor} waf={respuesta.waf or '-'} bytes={len(respuesta.texto)} ms={respuesta.ms}")
        if respuesta.ok and respuesta.texto:
            filas = []
            if respuesta.texto.lstrip().startswith("["):
                try:
                    filas = extraccion.vtex(json.loads(respuesta.texto), t.nombre, t.home)
                except ValueError:
                    filas = []
            filas = filas or extraccion.jsonld(respuesta.texto, t.nombre, t.home) or extraccion.selectores(respuesta.texto, t.nombre, t.home, t.selectores)
            print(f"  filas extraídas: {len(filas)}")
            for fila in filas[:3]:
                print(f"  - {fila['precio_actual']:>9.2f} | desc {fila['descuento']:>2}% | {fila['titulo'][:70]}")
            if args.guardar:
                Path(f"probe_{t.id}.html").write_text(respuesta.texto, encoding="utf-8")
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    objetivo = seleccionar(args.tiendas)
    if not objetivo:
        print("sin tiendas seleccionadas")
        return 2
    transporte = _transporte()
    filas: list[dict] = []
    estados: dict[str, dict] = {}
    for t in objetivo:
        adaptador = AdaptadorTienda(t, None, transporte)
        filas.extend(adaptador.discover())
        estados[t.id] = adaptador.last_status
    from .extraccion import deduplicar

    filas = deduplicar(filas)
    if args.out:
        Path(args.out).write_text(json.dumps({"total": len(filas), "estados": estados, "ofertas": filas}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(filas)} ofertas -> {args.out}")
    else:
        print(json.dumps({"total": len(filas), "estados": estados}, ensure_ascii=False, indent=2))
    return 0


def cmd_report(_: argparse.Namespace) -> int:
    enfriados = Cooldown().listar()
    print(f"hosts en enfriamiento: {len(enfriados)}")
    for host, datos in enfriados.items():
        print(f"  {host:40} {datos.get('motivo')} {datos.get('waf') or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="perimetro", description="Perímetro de tiendas con transporte stealth")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("doctor", help="estado de dependencias, proxies y tiendas").set_defaults(func=cmd_doctor)
    probe = sub.add_parser("probe", help="una petición de prueba y conteo de extracción")
    probe.add_argument("--tienda", nargs="*", default=["todas"])
    probe.add_argument("--q", default="liquidacion")
    probe.add_argument("--forzar", action="store_true")
    probe.add_argument("--guardar", action="store_true")
    probe.set_defaults(func=cmd_probe)
    crawl = sub.add_parser("crawl", help="recorre tiendas y escribe ofertas")
    crawl.add_argument("--tiendas", nargs="*", default=["todas"])
    crawl.add_argument("--q", default="")
    crawl.add_argument("--max", type=int, default=40)
    crawl.add_argument("--out", default="")
    crawl.set_defaults(func=cmd_crawl)
    sub.add_parser("report", help="hosts en cooldown").set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    if getattr(args, "q", ""):
        os.environ["PERIMETRO_QUERIES"] = args.q
    if hasattr(args, "max"):
        os.environ.setdefault("PERIMETRO_MAX_POR_TIENDA", str(args.max))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
<<<ENDFILE>>>
<<<FILE:perimetro/coverage.py>>>
# perimetro/coverage.py
from __future__ import annotations

from typing import Any

from .registry import recargar


def huecos() -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []
    for t in recargar():
        faltas = []
        if not t.busqueda:
            faltas.append("sin_url_busqueda")
        if not t.selectores.get("card") and not t.vtex_host and not t.sfcc_site:
            faltas.append("sin_selector_ni_catalogo")
        if t.requiere_js and not t.selectores.get("card"):
            faltas.append("js_sin_card")
        if faltas:
            salida.append({"id": t.id, "nombre": t.nombre, "faltas": faltas})
    return salida


def resumen() -> dict[str, Any]:
    tiendas = recargar()
    por_motor: dict[str, int] = {}
    for t in tiendas:
        por_motor[t.estrategias[0]] = por_motor.get(t.estrategias[0], 0) + 1
    return {"tiendas": len(tiendas), "por_motor_primario": por_motor, "con_vtex": sum(1 for t in tiendas if t.vtex_host), "con_sfcc": sum(1 for t in tiendas if t.sfcc_site), "requieren_js": sum(1 for t in tiendas if t.requiere_js), "huecos": huecos()}
<<<ENDFILE>>>
<<<FILE:core/scrapling_motor.py>>>
# core/scrapling_motor.py
# Motor de obtención primario basado en Scrapling, con degradación al método actual.
#
# Reglas:
#   1. Scrapling primero; si falla o devuelve 0 filas, se ejecuta el respaldo legacy.
#   2. Un perfil de dispositivo es coherente (UA + sec-ch-ua + plataforma + viewport + DPR).
#   3. El timeout se adapta a la latencia observada por host y respeta Retry-After.
#   4. Un bloqueo cuesta un cooldown de host, no un ciclo completo.
#   5. Solo se parsean nodos de tarjeta; nunca se recorre el DOM completo dos veces.
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger("DexterH4ck.Motor")

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CONFIG = Path(os.environ.get("SCRAPLING_CONFIG", "") or RAIZ / "config" / "scrapling_motor.json")
RUTA_COOLDOWN = Path(os.environ.get("MOTOR_COOLDOWN", "") or RAIZ / "motor_cooldown.json")

ESTADOS_BLOQUEO = {"blocked_waf", "captcha_required", "forbidden", "unauthorized", "rate_limited"}
ESTADOS_REINTENTO = {"timeout", "server_error", "network_error", "sin_motor"}

try:
    from core.source_resilience import classify_status  # type: ignore
except ImportError:  # pragma: no cover
    def classify_status(codigo: int) -> str:  # type: ignore
        if codigo == 200:
            return "ok"
        if codigo == 429:
            return "rate_limited"
        if codigo in (401, 407):
            return "unauthorized"
        if codigo == 403:
            return "forbidden"
        if codigo == 404:
            return "not_found"
        if codigo >= 500:
            return "server_error"
        if 300 <= codigo < 400:
            return "redirect"
        return f"http_{codigo}"

try:
    from perimetro import waf as _waf  # type: ignore
except Exception:  # pragma: no cover
    _waf = None

FIRMAS_WAF = (
    ("captcha_required", ("g-recaptcha", "hcaptcha", "cf-turnstile", "captcha-delivery", "px-captcha")),
    ("blocked_waf", ("just a moment", "checking your browser", "access denied", "_incapsula_resource", "datadome", "x-kpsdk")),
    ("rate_limited", ("too many requests", "rate limit", "exceeded")),
)


def _si(nombre: str, defecto: bool = False) -> bool:
    crudo = os.environ.get(nombre)
    if crudo is None:
        return defecto
    return crudo.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def clasificar(codigo: int, cabeceras: dict[str, str] | None, cuerpo: str) -> str:
    if _waf is not None:
        return _waf.clasificar(codigo, cabeceras, cuerpo)
    base = classify_status(codigo)
    if base != "ok":
        return base
    muestra = (cuerpo or "")[:30000].lower()
    for estado, firmas in FIRMAS_WAF:
        if any(firma in muestra for firma in firmas):
            return estado
    return "ok"


def _env_int(nombre: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        return max(minimo, min(maximo, int(os.environ.get(nombre, "") or defecto)))
    except (TypeError, ValueError):
        return defecto


try:
    from scrapling.fetchers import AsyncFetcherSession as _SesionHttp  # type: ignore
except Exception:  # pragma: no cover
    _SesionHttp = None
try:
    from scrapling.fetchers import AsyncStealthySession as _SesionStealth  # type: ignore
except Exception:  # pragma: no cover
    try:
        from scrapling.fetchers import StealthySession as _SesionStealth  # type: ignore
    except Exception:
        _SesionStealth = None
try:
    from scrapling.parser import Selector as _Selector  # type: ignore
except Exception:  # pragma: no cover
    _Selector = None


def disponible() -> dict[str, bool]:
    return {"http": _SesionHttp is not None, "stealth": _SesionStealth is not None, "selector": _Selector is not None}


@dataclass(frozen=True)
class Perfil:
    id: str
    ua: str
    plataforma: str
    sec_ch_ua: str
    movil: bool
    ancho: int
    alto: int
    dpr: float
    impersonate: str
    dispositivo: str = "desktop"

    def cabeceras(self, referer: str = "") -> dict[str, str]:
        base = {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
            "Sec-Ch-Ua-Mobile": "?1" if self.movil else "?0",
            "Sec-Ch-Ua-Platform": f'"{self.plataforma}"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }
        if self.sec_ch_ua:
            base["Sec-Ch-Ua"] = self.sec_ch_ua
        if referer:
            base["Referer"] = referer
        return base

    def viewport(self) -> dict[str, int]:
        return {"width": self.ancho, "height": self.alto}


def _perfil_desde(bruto: dict) -> Perfil | None:
    try:
        vista = bruto.get("viewport") or {}
        return Perfil(
            id=str(bruto["id"]),
            ua=str(bruto["ua"]),
            plataforma=str(bruto.get("plataforma") or "Windows"),
            sec_ch_ua=str(bruto.get("sec_ch_ua") or ""),
            movil=bool(bruto.get("movil")),
            ancho=int(vista.get("width") or 1366),
            alto=int(vista.get("height") or 768),
            dpr=float(bruto.get("dpr") or 1.0),
            impersonate=str(bruto.get("impersonate") or "chrome"),
            dispositivo=str(bruto.get("dispositivo") or "desktop"),
        )
    except (KeyError, TypeError, ValueError):
        return None


class Config:
    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = Path(ruta or RUTA_CONFIG)
        data: dict[str, Any] = {}
        if self.ruta.is_file():
            try:
                data = json.loads(self.ruta.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("config ilegible (%s); valores por defecto", type(exc).__name__)
        self.data = data if isinstance(data, dict) else {}
        self.perfiles = [p for p in (_perfil_desde(b) for b in self.data.get("perfiles") or [] if isinstance(b, dict)) if p]
        if not self.perfiles:
            self.perfiles = [Perfil("desktop_chrome_win", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36", "Windows", "", False, 1366, 768, 1.0, "chrome")]
        self._pedidos = [p.strip() for p in (os.environ.get("SCRAPLING_PERFILES") or "").split(",") if p.strip()]
        self.presupuesto = self.data.get("presupuesto") or {}
        self.esperas = self.data.get("esperas") or {}
        self.dominios = self.data.get("dominios") or {}
        self.perfil_por_host = self.data.get("perfil_por_host") or {}
        self.tiendas = self.data.get("tiendas") or {}

    @property
    def modo(self) -> str:
        return (os.environ.get("SCRAPLING_MOTOR") or self.data.get("modo") or "auto").strip().lower()

    @property
    def concurrencia(self) -> int:
        return _env_int("SCRAPLING_CONCURRENCIA", int(self.data.get("concurrencia") or 4), 1, 12)

    @property
    def rotar_cada(self) -> int:
        return _env_int("SCRAPLING_ROTAR_CADA", int(self.data.get("rotar_perfil_cada") or 3), 1, 50)

    @property
    def fallback(self) -> bool:
        return _si("SCRAPLING_FALLBACK", bool(self.data.get("fallback_habilitado", True)))

    @property
    def reintentos(self) -> int:
        return _env_int("SCRAPLING_REINTENTOS", int(self.data.get("reintentos") or 2), 0, 4)

    def perfiles_activos(self) -> list[Perfil]:
        if not self._pedidos:
            return self.perfiles
        indice = {p.id: p for p in self.perfiles}
        elegidos = [indice[i] for i in self._pedidos if i in indice]
        return elegidos or self.perfiles

    def perfil_de(self, host: str) -> Perfil:
        activos = self.perfiles_activos()
        for dominio, ident in self.perfil_por_host.items():
            if host.endswith(dominio):
                for perfil in activos:
                    if perfil.id == ident:
                        return perfil
        return activos[0]

    def requiere_navegador(self, host: str) -> bool:
        for dominio in self.dominios.get("nunca_navegador") or []:
            if host.endswith(dominio):
                return False
        return any(host.endswith(d) for d in self.dominios.get("requieren_navegador") or [])

    def resolver_cloudflare(self, host: str) -> bool:
        crudo = (os.environ.get("SCRAPLING_SOLVE_CLOUDFLARE") or "").strip().lower()
        if crudo in ("1", "true", "si", "sí"):
            return True
        if crudo in ("0", "false", "no"):
            return False
        return any(host.endswith(d) for d in self.dominios.get("solve_cloudflare") or [])


class Presupuesto:
    def __init__(self, conf: dict[str, Any]) -> None:
        self.base = float(conf.get("base_ms") or 20000)
        self.minimo = float(conf.get("min_ms") or 8000)
        self.maximo = float(conf.get("max_ms") or 60000)
        self.factor = float(conf.get("factor_ewma") or 1.8)
        self.alfa = float(conf.get("alfa_ewma") or 0.3)
        self.tope_retry = float(conf.get("tope_retry_after_s") or 45)
        self._ewma: dict[str, float] = {}
        self._lock = threading.Lock()

    def timeout(self, host: str) -> float:
        with self._lock:
            referencia = self._ewma.get(host)
        if not referencia:
            return self.base
        return max(self.minimo, min(self.maximo, referencia * self.factor))

    def observar(self, host: str, ms: float) -> None:
        with self._lock:
            previo = self._ewma.get(host, ms)
            self._ewma[host] = (1 - self.alfa) * previo + self.alfa * ms

    def espera_retry_after(self, valor: str | None) -> float:
        if not valor:
            return 0.0
        try:
            return min(self.tope_retry, float(str(valor).strip().rstrip("s")))
        except ValueError:
            return 0.0


class Rotador:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._contador: dict[str, int] = {}
        self._asignado: dict[str, Perfil] = {}
        self._lock = threading.Lock()

    def siguiente(self, host: str) -> Perfil:
        with self._lock:
            actual = self._asignado.get(host)
            cuenta = self._contador.get(host, 0) + 1
            self._contador[host] = cuenta
            if actual is None or cuenta % self.config.rotar_cada == 0:
                activos = self.config.perfiles_activos()
                actual = self.config.perfil_de(host)
                otros = [p for p in activos if p.id != actual.id]
                if otros and cuenta > 1:
                    actual = random.choice(otros)
                self._asignado[host] = actual
            return actual


class Cooldown:
    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = Path(ruta or RUTA_COOLDOWN)
        try:
            self.minutos = max(1, int(os.environ.get("MOTOR_COOLDOWN_MIN", "") or 30))
        except (TypeError, ValueError):
            self.minutos = 30

    def _leer(self) -> dict[str, dict]:
        try:
            data = json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def activo(self, host: str) -> dict | None:
        entrada = self._leer().get(host)
        if entrada and float(entrada.get("hasta", 0)) > time.time():
            return entrada
        return None

    def registrar(self, host: str, motivo: str) -> None:
        data = self._leer()
        data[host] = {"motivo": motivo, "hasta": time.time() + self.minutos * 60}
        try:
            self.ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def limpiar(self, host: str) -> None:
        data = self._leer()
        if data.pop(host, None) is not None:
            try:
                self.ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass


@dataclass
class Resultado:
    ok: bool = False
    estado: str = "sin_intento"
    http: int = 0
    texto: str = ""
    filas: list[dict] = field(default_factory=list)
    motor: str = ""
    perfil: str = ""
    fallback: bool = False
    ms: int = 0
    detalle: str = ""

    @property
    def bloqueado(self) -> bool:
        return self.estado in ESTADOS_BLOQUEO


def _host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0].lower()


class Motor:
    def __init__(self, config: Config | None = None, proxies: list[dict] | None = None) -> None:
        self.config = config or Config()
        self.presupuesto = Presupuesto(self.config.presupuesto)
        self.rotador = Rotador(self.config)
        self.cooldown = Cooldown()
        self.proxies = proxies if proxies is not None else self._proxies_entorno()
        self._turno = 0
        self._semaforo: asyncio.Semaphore | None = None
        self._loop: Any = None
        self.ultimo: dict[str, Resultado] = {}

    @staticmethod
    def _proxies_entorno() -> list[dict]:
        crudo = (os.environ.get("PROXY_POOL") or "").strip()
        if not crudo:
            return []
        if crudo.startswith("["):
            try:
                return [p for p in json.loads(crudo) if isinstance(p, dict) and p.get("server")]
            except ValueError:
                return []
        return [{"server": t.strip() if "://" in t else "http://" + t.strip()} for t in crudo.replace("\n", ",").split(",") if t.strip()]

    def _proxy(self) -> dict | None:
        if not self.proxies:
            return None
        self._turno = (self._turno + 1) % len(self.proxies)
        return self.proxies[self._turno]

    def _semaforo_actual(self) -> asyncio.Semaphore:
        loop = asyncio.get_event_loop()
        if self._semaforo is None or self._loop is not loop:
            self._semaforo = asyncio.Semaphore(self.config.concurrencia)
            self._loop = loop
        return self._semaforo

    async def pedir(self, url: str, *, perfil: Perfil | None = None, referer: str = "", espera_selector: str = "", forzar_cooldown: bool = False) -> Resultado:
        host = _host(url)
        enfriado = self.cooldown.activo(host)
        if enfriado and not forzar_cooldown:
            return Resultado(estado="cooldown", detalle=str(enfriado.get("motivo")))

        hay = disponible()
        if not hay["http"] and not hay["stealth"]:
            return Resultado(estado="sin_motor", detalle="scrapling no instalado")

        elegido = perfil or self.rotador.siguiente(host)
        modo = self.config.modo
        if modo in ("auto", ""):
            modo = "stealth" if self.config.requiere_navegador(host) and hay["stealth"] else "http"
        if modo == "stealth" and not hay["stealth"]:
            modo = "http"

        ultimo = Resultado(estado="sin_intento")
        for intento in range(self.config.reintentos + 1):
            resultado = await self._ejecutar(url, elegido, modo, referer, espera_selector)
            resultado.perfil = elegido.id
            ultimo = resultado
            self.ultimo[host] = resultado
            if resultado.ok:
                self.cooldown.limpiar(host)
                return resultado
            if resultado.bloqueado:
                self.cooldown.registrar(host, resultado.estado)
                return resultado
            if resultado.estado in ESTADOS_REINTENTO and intento < self.config.reintentos:
                espera = self.presupuesto.espera_retry_after(resultado.detalle) if resultado.estado == "rate_limited" else 0
                await asyncio.sleep(espera or min(12.0, 1.3 * (2 ** intento)) + random.uniform(0.2, 0.9))
                elegido = self.rotador.siguiente(host)
        return ultimo

    async def _ejecutar(self, url: str, perfil: Perfil, modo: str, referer: str, espera_selector: str) -> Resultado:
        host = _host(url)
        timeout = self.presupuesto.timeout(host) / 1000.0
        inicio = time.monotonic()
        async with self._semaforo_actual():
            try:
                crudo = await self._llamar(url, perfil, modo, perfil.cabeceras(referer), timeout, espera_selector)
            except asyncio.TimeoutError:
                return Resultado(estado="timeout", motor=modo, ms=_ms(inicio))
            except Exception as exc:
                nombre = type(exc).__name__
                return Resultado(estado="timeout" if "timeout" in nombre.lower() else "network_error", motor=modo, ms=_ms(inicio), detalle=nombre)

        ms = _ms(inicio)
        self.presupuesto.observar(host, ms)
        codigo = int(getattr(crudo, "status", getattr(crudo, "status_code", 0)) or 0)
        texto = _cuerpo(crudo)
        cabeceras = _cabeceras(crudo)
        estado = clasificar(codigo, cabeceras, texto)
        detalle = cabeceras.get("retry-after", "") if estado == "rate_limited" else ""
        return Resultado(ok=estado == "ok", estado=estado, http=codigo, texto=texto, motor=modo, ms=ms, detalle=detalle)

    async def _llamar(self, url: str, perfil: Perfil, modo: str, cabeceras: dict, timeout: float, espera_selector: str) -> Any:
        proxy = self._proxy()
        if modo == "stealth":
            opciones: dict[str, Any] = {
                "headless": True,
                "humanize": True,
                "geoip": True,
                "block_webrtc": True,
                "hide_canvas": True,
                "allow_webgl": True,
                "network_idle": True,
                "real_challenge": True,
                "timeout": int(timeout * 1000),
                "wait": int(self.config.esperas.get("tras_carga_ms") or 1200),
                "extra_headers": cabeceras,
                "useragent": perfil.ua,
                "locale": "es-MX",
                "timezone_id": "America/Mexico_City",
            }
            if espera_selector:
                opciones["wait_selector"] = espera_selector
                opciones["wait_selector_state"] = "attached"
            if self.config.resolver_cloudflare(_host(url)):
                opciones["solve_cloudflare"] = True
            if proxy:
                opciones["proxy"] = proxy
            return await _abrir(_SesionStealth, url, opciones, "fetch")

        opciones = {"stealthy_headers": True, "impersonate": perfil.impersonate, "http3": True, "follow_redirects": True, "headers": cabeceras, "timeout": timeout}
        if proxy:
            opciones["proxy"] = proxy
        return await _abrir(_SesionHttp, url, opciones, "get")

    async def pedir_o_fallback(self, url: str, respaldo: Callable[[], Any] | None = None, *, perfil: Perfil | None = None, referer: str = "", espera_selector: str = "", filas_de: Callable[[Resultado], list[dict]] | None = None) -> Resultado:
        resultado = await self.pedir(url, perfil=perfil, referer=referer, espera_selector=espera_selector)
        if filas_de is not None:
            try:
                resultado.filas = filas_de(resultado)
            except Exception as exc:
                resultado.filas = []
                resultado.detalle = f"extraccion:{type(exc).__name__}"
        if resultado.filas:
            return resultado
        if not self.config.fallback or respaldo is None:
            return resultado
        try:
            heredado = respaldo()
            if asyncio.iscoroutine(heredado):
                heredado = await heredado
        except Exception as exc:
            resultado.detalle = f"fallback_error:{type(exc).__name__}"
            return resultado
        if isinstance(heredado, Resultado):
            heredado.fallback = True
            return heredado
        if hasattr(heredado, "ok") and hasattr(heredado, "estado"):
            return Resultado(ok=bool(getattr(heredado, "ok", False)), estado=str(getattr(heredado, "estado", "fallback")), http=int(getattr(heredado, "http", 0) or 0), texto=str(getattr(heredado, "texto", "") or ""), motor="legacy", fallback=True, detalle="respaldo_pipeline")
        if isinstance(heredado, list):
            resultado.filas = heredado
            resultado.fallback = True
            resultado.detalle = "respaldo_pipeline"
            resultado.ok = resultado.ok or bool(heredado)
            return resultado
        resultado.detalle = "respaldo_sin_datos"
        return resultado

    def pedir_sync(self, url: str, **kw: Any) -> Resultado:
        return _correr(self.pedir(url, **kw))

    def pedir_o_fallback_sync(self, url: str, respaldo: Callable[[], Any] | None = None, **kw: Any) -> Resultado:
        return _correr(self.pedir_o_fallback(url, respaldo, **kw))

    def lote_sync(self, urls: Iterable[str], **kw: Any) -> list[Resultado]:
        return _correr(self.lote(urls, **kw))

    async def lote(self, urls: Iterable[str], **kw: Any) -> list[Resultado]:
        return await asyncio.gather(*(self.pedir(u, **kw) for u in urls))

    def extraer(self, resultado: Resultado, selectores: dict[str, str]) -> list[dict]:
        if not resultado.texto:
            return []
        return extraer_minimo(resultado.texto, selectores)


PRECIO = re.compile(r"\$\s*([\d][\d.,]{1,14})")


def _a_precio(texto: Any) -> float | None:
    try:
        from core.price_sanitizer import parse_price  # type: ignore

        return parse_price(texto)
    except Exception:
        pass
    if texto is None:
        return None
    try:
        valor = float(re.sub(r"[^\d.]", "", str(texto)))
    except ValueError:
        return None
    return valor if 0 < valor < 2_000_000 else None


def _texto_plano(trozo: str) -> str:
    import html as _html

    limpio = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", trozo, flags=re.S | re.I)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", limpio))).strip()


def extraer_minimo(cuerpo: str, selectores: dict[str, str]) -> list[dict]:
    """Solo nodos de tarjeta. Usa Selector de Scrapling; si no, el extractor de perimetro."""
    regla = selectores.get("card") or ""
    if not regla:
        return []
    if _Selector is not None:
        try:
            arbol = _Selector(cuerpo)
            filas = []
            for nodo in arbol.css(regla):
                enlace = ""
                for ancla in nodo.css(selectores.get("link") or "a[href]"):
                    enlace = ancla.attrib.get("href") or ""
                    if enlace:
                        break
                titulo = ""
                for titulo_nodo in nodo.css(selectores.get("title") or "a"):
                    titulo = (titulo_nodo.text or "").strip() or titulo_nodo.attrib.get("title", "")
                    if titulo:
                        break
                precio = None
                if selectores.get("price"):
                    for nodo_precio in nodo.css(selectores["price"]):
                        precio = _a_precio((nodo_precio.text or "").strip())
                        if precio:
                            break
                if precio is None:
                    limpios = [p for p in (_a_precio(m) for m in PRECIO.findall(_texto_plano(str(nodo.html_content or "")))) if p]
                    precio = min(limpios) if limpios else None
                if titulo and enlace and precio:
                    filas.append({"titulo": titulo, "url": enlace, "precio_actual": precio})
            return filas
        except Exception:
            pass
    try:
        from perimetro.extraccion import selectores as _sel  # type: ignore

        return _sel(cuerpo, "", "", selectores)
    except Exception:
        return []


def _cuerpo(respuesta: Any) -> str:
    for atributo in ("body", "html_content", "text"):
        valor = getattr(respuesta, atributo, None)
        if isinstance(valor, bytes):
            return valor.decode("utf-8", "replace")
        if isinstance(valor, str):
            return valor
    return str(respuesta or "")


def _cabeceras(respuesta: Any) -> dict[str, str]:
    try:
        return {str(k).lower(): str(v) for k, v in dict(getattr(respuesta, "headers", {}) or {}).items()}
    except Exception:
        return {}


def _ms(inicio: float) -> int:
    return int((time.monotonic() - inicio) * 1000)


async def _abrir(clase: Any, url: str, opciones: dict, metodo: str) -> Any:
    if clase is None:
        raise RuntimeError("motor no disponible")
    sesion = clase(**opciones)
    if hasattr(sesion, "__aenter__"):
        async with sesion as abierta:
            llamada = getattr(abierta, metodo, None) or getattr(abierta, "fetch", None) or getattr(abierta, "get", None)
            resultado = llamada(url)
            if asyncio.iscoroutine(resultado):
                resultado = await resultado
        return resultado
    with sesion as abierta:
        llamada = getattr(abierta, metodo, None) or getattr(abierta, "fetch", None) or getattr(abierta, "get", None)
        return llamada(url)


def _correr(coro: Awaitable[Any]) -> Any:
    try:
        return asyncio.run(coro)  # type: ignore[arg-type]
    except RuntimeError:
        bucle = asyncio.new_event_loop()
        try:
            return bucle.run_until_complete(coro)  # type: ignore[arg-type]
        finally:
            bucle.close()


_MOTOR: Motor | None = None
_LOCK = threading.Lock()


def motor() -> Motor:
    global _MOTOR
    with _LOCK:
        if _MOTOR is None:
            _MOTOR = Motor()
        return _MOTOR


def reiniciar() -> None:
    global _MOTOR
    with _LOCK:
        _MOTOR = None
<<<ENDFILE>>>
<<<FILE:scrapers/scrapling_adaptador.py>>>
# scrapers/scrapling_adaptador.py
# Adaptadores por tienda sobre el motor. Si el motor falla, cae al scraper legacy
# equivalente y lo marca en last_status (nunca se queda mudo).
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from core.scraper_base import BaseScraper, ScraperContext, run_scrapers_parallel
from core.scrapling_motor import Config, Motor, Resultado, disponible, motor as motor_global

logger = logging.getLogger("DexterH4ck.AdaptadorScrapling")

TERMINOS = ("liquidacion", "remate", "outlet")


def _terminos(tienda: str) -> tuple[str, ...]:
    crudo = os.environ.get(f"{tienda.upper()}_QUERIES", "") or os.environ.get("SCRAPLING_QUERIES", "")
    pedidos = tuple(t.strip() for t in crudo.split(",") if t.strip())
    return pedidos or TERMINOS


def _entero(nombre: str, defecto: int, minimo: int = 1, maximo: int = 20) -> int:
    try:
        return max(minimo, min(maximo, int(os.environ.get(nombre, "") or defecto)))
    except (TypeError, ValueError):
        return defecto


class AdaptadorScrapling(BaseScraper):
    tienda = "unknown"

    def __init__(self, contexto: ScraperContext | None = None, motor: Motor | None = None, respaldo: Callable[[], list[dict]] | None = None, selectores: dict[str, str] | None = None) -> None:
        super().__init__(contexto)
        self.store = self.tienda
        self._motor = motor
        self._respaldo = respaldo
        self.selectores = selectores or {}
        self.last_status: dict[str, Any] = {"state": "not_run"}

    @property
    def motor(self) -> Motor:
        if self._motor is None:
            self._motor = motor_global()
        return self._motor

    def _cfg(self) -> dict:
        return (self.motor.config.tiendas or {}).get(self.tienda.lower(), {})

    def _url(self, termino: str) -> str:
        return (self._cfg().get("url") or "").replace("{q}", termino.replace(" ", "+"))

    def discover(self) -> list[dict[str, Any]]:
        if not self._cfg().get("url"):
            self.last_status = {"state": "not_configured", "detail": "sin url en config/scrapling_motor.json"}
            return self._legacy("not_configured")

        limite = _entero("SCRAPLING_MAX_POR_TIENDA", 30)
        filas: list[dict] = []
        for termino in _terminos(self.tienda):
            resultado = self.motor.pedir_o_fallback_sync(
                self._url(termino),
                self._respaldo,
                espera_selector=self.selectores.get("card", ""),
                filas_de=lambda r: self._extraer(r),
            )
            if resultado.filas:
                filas.extend(resultado.filas)
            self._anotar(resultado)
            if resultado.filas or resultado.bloqueado or len(filas) >= limite:
                break

        filas = self._normalizar(filas)[:limite]
        if filas:
            self.last_status.update({"state": "ok", "count": len(filas)})
        return filas

    def _extraer(self, resultado: Resultado) -> list[dict]:
        if not resultado.texto or not self.selectores:
            return []
        return self._normalizar(self.motor.extraer(resultado, self.selectores))

    def _normalizar(self, filas: list[dict]) -> list[dict]:
        salida = []
        for fila in filas:
            if not fila.get("titulo") or not fila.get("url"):
                continue
            if not str(fila["url"]).startswith("http"):
                base = (self._cfg().get("url") or "").split("/search")[0].split("/buscar")[0].rstrip("/")
                fila["url"] = base + "/" + str(fila["url"]).lstrip("/")
            fila.setdefault("tienda", self.tienda)
            fila.setdefault("origen_link", "motor_scrapling")
            fila.setdefault("tipo_fuente", "ONLINE")
            salida.append(fila)
        return salida

    def _anotar(self, resultado: Resultado) -> None:
        self.last_status = {"state": "ok" if resultado.ok else resultado.estado, "motor": resultado.motor or "scrapling", "perfil": resultado.perfil, "fallback": resultado.fallback, "http": resultado.http}

    def _legacy(self, motivo: str) -> list[dict]:
        if self._respaldo is None:
            return []
        try:
            filas = self._respaldo() or []
        except Exception as exc:
            self.last_status = {"state": "error", "detail": type(exc).__name__}
            return []
        self.last_status = {"state": "fallback", "detail": motivo, "fallback": True, "count": len(filas)}
        return filas


def _respaldo_de(nombre: str) -> Callable[[], list[dict]] | None:
    try:
        from scrapers import api_stores

        for scraper in api_stores.api_first_scrapers(None):
            if scraper.store.lower() == nombre.lower() and not getattr(scraper, "tienda", None):
                return scraper.discover
    except Exception:
        return None
    return None


def _selectores(ident: str) -> dict[str, str]:
    comunes = {"card": "div[class*=product]", "title": "a[class*=name]", "price": "span[class*=price]", "link": "a[href]"}
    especiales = {
        "walmart": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]"},
        "bodega": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]"},
        "sams": {"card": "div[data-testid=\"item-stacked-label\"]", "title": "span.lh-title", "price": "div[data-automation-id=\"product-price\"]"},
        "amazon": {"card": "div[data-component-type=\"s-search-result\"]", "title": "h2 span", "price": "span.a-price-whole", "link": "h2 a"},
        "nike": {"card": "div.product-card", "title": "div.product-card__title", "price": "div.product-price", "link": "a.product-card__link-overlay"},
        "adidas": {"card": "div[data-auto-id=\"product-card\"]", "title": "div[data-auto-id=\"product-card-description\"] span", "price": "div[data-auto-id=\"product-price\"]", "link": "a[href]"},
        "palacio": {"card": "div.product-tile", "title": "a.link", "price": "span.product-sales-price", "link": "a.link"},
        "officedepot": {"card": "div.product-tile", "title": "a.link", "price": "span.product-sales-price", "link": "a.link"},
    }
    return {**comunes, **especiales.get(ident, {})}


def adaptadores_motor(contexto: ScraperContext | None = None) -> list[AdaptadorScrapling]:
    config = Config()
    compartido = Motor(config)
    salida = []
    for ident in config.tiendas:
        clase = type(f"Adaptador_{ident}", (AdaptadorScrapling,), {"tienda": ident, "selectores": _selectores(ident)})
        salida.append(clase(contexto, compartido, _respaldo_de(ident)))
    return salida


class MotorScraper:
    def __init__(self, contexto: ScraperContext | None = None) -> None:
        self.scrapers = adaptadores_motor(contexto)

    def discover(self) -> list[dict[str, Any]]:
        if not any(disponible().values()):
            logger.warning("Scrapling no disponible; todos los adaptadores caerán al respaldo.")
        return run_scrapers_parallel(self.scrapers, max_workers=_entero("SCRAPLING_CONCURRENCIA", 4, 1, 8))

    def estado(self) -> dict[str, Any]:
        return {s.store: s.last_status for s in self.scrapers}
<<<ENDFILE>>>
<<<FILE:tools/verificar_motor.py>>>
# tools/verificar_motor.py
from __future__ import annotations

import json
import sys

from core.scrapling_motor import Config, Presupuesto, disponible, extraer_minimo


def main() -> int:
    deps = disponible()
    print("dependencias:", json.dumps(deps))
    if not any(deps.values()):
        print("::warning::Scrapling no disponible; el pipeline usará el respaldo legacy.")

    config = Config()
    print(f"perfiles: {len(config.perfiles)} | activos: {len(config.perfiles_activos())} | concurrencia: {config.concurrencia}")
    for perfil in config.perfiles_activos():
        print(f"  {perfil.id:20} {perfil.dispositivo:8} dpr={perfil.dpr} {perfil.ua[:48]}")

    presupuesto = Presupuesto(config.presupuesto)
    print("timeout inicial:", presupuesto.timeout("ejemplo.mx"))
    presupuesto.observar("ejemplo.mx", 5000)
    print("timeout tras latencia:", presupuesto.timeout("ejemplo.mx"))
    print("retry-after 10 ->", presupuesto.espera_retry_after("10"))

    html = '<div class="product"><a class="name" href="/p/1">Cafetera</a><span class="price">$1,299.00</span></div>'
    filas = extraer_minimo(html, {"card": "div.product", "title": "a.name", "price": "span.price", "link": "a.name"})
    print("extraccion:", json.dumps(filas, ensure_ascii=False))
    if not filas:
        print("::error::La extracción mínima no devolvió filas.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
<<<ENDFILE>>>
<<<FILE:requirements-perimetro.txt>>>
# Capa perímetro (aislada del núcleo actual).
scrapling[fetchers]>=0.4.2
curl_cffi>=0.7.4
patchright>=1.48
playwright>=1.48
<<<ENDFILE>>>
<<<FILE:requirements-scrapling.txt>>>
# Motor Scrapling (primario). Requiere además: scrapling install
scrapling[fetchers]>=0.4.2
curl_cffi>=0.7.4
patchright>=1.48
playwright>=1.48
<<<ENDFILE>>>
<<<FILE:tests/test_perimetro.py>>>
# tests/test_perimetro.py
# unittest puro: el workflow corre "unittest discover", no pytest.
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from perimetro import extraccion, registry, waf
from perimetro.registry import Tienda

RAIZ = Path(__file__).resolve().parent.parent


class PruebasPerimetro(unittest.TestCase):
    def setUp(self):
        self.tiendas = registry.recargar()

    def test_perimetro_completo(self):
        esperadas = {"walmart", "bodega", "chedraui", "soriana", "sams", "coppel", "elektra", "liverpool", "suburbia", "sears", "nike", "adidas", "palacio", "homedepot", "officedepot", "lacomer", "amazon"}
        self.assertTrue(esperadas.issubset({t.id for t in self.tiendas}))

    def test_estrategias_validas(self):
        for t in self.tiendas:
            self.assertTrue(t.estrategias, t.id)
            self.assertTrue(set(t.estrategias) <= registry.ESTRATEGIAS_VALIDAS, t.id)
            self.assertTrue(t.busqueda or t.vtex_host or t.sfcc_site, t.id)

    def test_url_busqueda_codifica(self):
        t = Tienda(id="x", nombre="X", home="https://x.mx", estrategias=("html",), busqueda="https://x.mx/s?q={q}")
        self.assertEqual(t.url_busqueda("liquidacion 50%"), "https://x.mx/s?q=liquidacion+50%25")

    def test_clasificar_challenge_es_bloqueo(self):
        self.assertEqual(waf.clasificar(200, {"cf-mitigated": "challenge"}, "Just a moment..."), "blocked_waf")
        self.assertEqual(waf.clasificar(200, {}, "please solve the g-recaptcha"), "captcha_required")
        self.assertEqual(waf.clasificar(200, {}, "<html>productos normales</html>"), "ok")

    def test_cooldown_ida_y_vuelta(self):
        with tempfile.TemporaryDirectory() as tmp:
            cooldown = waf.Cooldown(Path(tmp) / "c.json", minutos=5)
            cooldown.registrar("x.mx", "blocked_waf", "cloudflare")
            self.assertIsNotNone(cooldown.activo("x.mx"))
            cooldown.limpiar("x.mx")
            self.assertIsNone(cooldown.activo("x.mx"))

    def test_vtex_extrae_mejor_oferta(self):
        payload = [{"productId": "1", "productName": "Televisor", "link": "/p/televisor/1", "items": [{"sellers": [{"commertialOffer": {"Price": 1000, "ListPrice": 2000, "AvailableQuantity": 3}}, {"commertialOffer": {"Price": 800, "ListPrice": 2000, "AvailableQuantity": 1}}]}]}]
        filas = extraccion.vtex(payload, "Chedraui", "https://www.chedraui.com.mx")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["precio_actual"], 800)
        self.assertEqual(filas[0]["descuento"], 60)

    def test_jsonld_itemlist(self):
        cuerpo = '<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"ListItem","item":{"@type":"Product","name":"Silla","sku":"S1","offers":{"price":"450"},"url":"/p/silla/1"}}]}</script>'
        filas = extraccion.jsonld(cuerpo, "Sears", "https://www.sears.com.mx")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["precio_actual"], 450)

    def test_selectores_respaldo(self):
        cuerpo = '<div class="product"><a class="name" href="/p/x/9">Cafetera</a><span class="price">$1,299.00</span><s>$2,000.00</s><span class="pct">35% de descuento</span></div>'
        filas = extraccion.selectores(cuerpo, "Soriana", "https://www.soriana.com", {"card": "div.product", "title": "a.name", "price": "span.price", "link": "a.name"})
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["descuento"], 35)
        self.assertEqual(filas[0]["url"], "https://www.soriana.com/p/x/9")

    def test_deduplicar(self):
        filas = [{"id": "1", "url": "/a"}, {"id": "1", "url": "/a"}, {"id": "2", "url": "/b"}]
        self.assertEqual(len(extraccion.deduplicar(filas)), 2)

    def test_config_json_valido(self):
        data = json.loads((RAIZ / "config" / "perimetro_tiendas.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["tiendas"]), 17)


if __name__ == "__main__":
    unittest.main()
<<<ENDFILE>>>
<<<FILE:tests/test_scrapling_motor.py>>>
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
<<<ENDFILE>>>
<<<FILE:docs/PERIMETRO.md>>>
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
<<<ENDFILE>>>
'''\n\n
def extraer_payload() -> dict[str, str]:
    """Convierte los bloques <<<FILE:...>>> en un mapa ruta/contenido."""
    patron = re.compile(r"^<<<FILE:(.+?)>>>\r?\n(.*?)^<<<ENDFILE>>>\s*$", re.M | re.S)
    archivos: dict[str, str] = {}
    for coincidencia in patron.finditer(PAYLOAD):
        ruta = coincidencia.group(1).strip().replace("\\", "/")
        contenido = coincidencia.group(2)
        if not ruta or ruta.startswith("/") or ".." in Path(ruta).parts:
            raise ValueError(f"Ruta insegura en PAYLOAD: {ruta!r}")
        if ruta in archivos:
            raise ValueError(f"Ruta duplicada en PAYLOAD: {ruta}")
        archivos[ruta] = contenido.rstrip("\n") + "\n"
    if not archivos:
        raise ValueError("PAYLOAD no contiene bloques <<<FILE:...>>> válidos.")
    return archivos


def validar_payload(archivos: dict[str, str]) -> None:
    for ruta, contenido in archivos.items():
        if not contenido.strip():
            raise ValueError(f"Archivo vacío: {ruta}")
        if ruta.endswith(".json"):
            try:
                json.loads(contenido)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON inválido en {ruta}:{exc.lineno}:{exc.colno}: {exc.msg}") from exc
    print(f"Validación estructural correcta: {len(archivos)} archivos, sin rutas duplicadas ni JSON inválido.")


def escribir_zip(archivos: dict[str, str], destino: Path = Path("payload.zip")) -> None:
    raiz = Path("scrapling_payload")
    for ruta, contenido in archivos.items():
        archivo = raiz / ruta
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_text(contenido, encoding="utf-8")
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        for ruta in sorted(archivos):
            paquete.write(raiz / ruta, arcname=ruta)
    print(f"Payload escrito en {raiz}/ y {destino}.")


def github_request(token: str, metodo: str, endpoint: str, datos: dict | None = None):
    url = endpoint if endpoint.startswith("https://") else f"{API}{endpoint}"
    cuerpo = None if datos is None else json.dumps(datos).encode("utf-8")
    solicitud = urllib.request.Request(
        url,
        data=cuerpo,
        method=metodo,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "DexterH4ck-Scrapling-Installer",
            **({"Content-Type": "application/json"} if cuerpo is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(solicitud, timeout=45) as respuesta:
            bruto = respuesta.read().decode("utf-8", "replace")
            return json.loads(bruto) if bruto else {}
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")
        try:
            detalle = json.dumps(json.loads(detalle), ensure_ascii=False)
        except Exception:
            pass
        raise RuntimeError(f"GitHub API HTTP {exc.code} en {metodo} {endpoint}: {detalle[:1200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No se pudo conectar con GitHub: {exc.reason}") from exc


def publicar_github(archivos: dict[str, str], repo: str, rama: str, token: str, crear_pr: bool = True) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError(f"Repositorio inválido: {repo!r}")
    info = github_request(token, "GET", f"/repos/{repo}")
    base = info.get("default_branch") or "main"
    ref_base = github_request(token, "GET", f"/repos/{repo}/git/ref/heads/{base}")
    sha_base = ref_base["object"]["sha"]
    try:
        ref_rama = github_request(token, "GET", f"/repos/{repo}/git/ref/heads/{rama}")
        sha_padre = ref_rama["object"]["sha"]
        print(f"La rama {rama} ya existe; se actualizará.")
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        github_request(token, "POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{rama}", "sha": sha_base})
        sha_padre = sha_base
        print(f"Rama creada: {rama}")

    arbol = [{
        "path": ruta,
        "mode": "100644",
        "type": "blob",
        "content": contenido,
    } for ruta, contenido in sorted(archivos.items())]
    nuevo_arbol = github_request(
        token, "POST", f"/repos/{repo}/git/trees",
        {"base_tree": sha_padre, "tree": arbol},
    )["sha"]
    commit = github_request(
        token, "POST", f"/repos/{repo}/git/commits",
        {"message": "Añade perímetro y motor Scrapling con fallback", "tree": nuevo_arbol, "parents": [sha_padre]},
    )["sha"]
    github_request(token, "PATCH", f"/repos/{repo}/git/refs/heads/{rama}", {"sha": commit, "force": False})
    print(f"Commit publicado: {commit}")

    if not crear_pr:
        print("Publicación terminada (--no-pr).")
        return
    abiertos = github_request(token, "GET", f"/repos/{repo}/pulls?state=open&head={repo.split('/')[0]}:{rama}&base={base}")
    if abiertos:
        print(f"PR existente: {abiertos[0].get('html_url')}")
        return
    pr = github_request(
        token, "POST", f"/repos/{repo}/pulls",
        {
            "title": TITULO,
            "head": rama,
            "base": base,
            "body": CUERPO,
        },
    )
    print(f"Pull request creado: {pr.get('html_url', '(URL no devuelta)')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Genera o publica el perímetro y el motor Scrapling para DexterH4ck-ofertas."
    )
    parser.add_argument("--zip", action="store_true", help="extrae los archivos y crea payload.zip")
    parser.add_argument("--dry-run", action="store_true", help="valida y muestra los cambios sin escribir ni publicar")
    parser.add_argument("--list", action="store_true", help="lista los archivos incluidos")
    parser.add_argument("--rama", default=RAMA, help=f"rama destino (predeterminada: {RAMA})")
    parser.add_argument("--no-pr", action="store_true", help="publica la rama sin crear un pull request")
    parser.add_argument("--sin-parches", action="store_true", help="compatibilidad: no hay parches adicionales que aplicar")
    parser.add_argument("--token", default="", help="token GitHub; preferible usar GITHUB_TOKEN")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"repositorio destino (predeterminado: {DEFAULT_REPO})")
    args = parser.parse_args(argv)

    try:
        archivos = extraer_payload()
        validar_payload(archivos)
        if args.list:
            for ruta in sorted(archivos):
                print(ruta)
            return 0
        if args.dry_run:
            print(f"DRY RUN: se prepararían {len(archivos)} archivos en {args.repo}, rama {args.rama}.")
            for ruta in sorted(archivos):
                print(f"  + {ruta}")
            print("No se escribió ningún archivo ni se realizó ninguna llamada de escritura a GitHub.")
            return 0
        if args.zip:
            escribir_zip(archivos)
            return 0
        token = (args.token or os.environ.get("GITHUB_TOKEN", "")).strip()
        if not token:
            raise ValueError("Falta GITHUB_TOKEN. Configúralo como variable de entorno o usa --token.")
        publicar_github(archivos, args.repo, args.rama, token, crear_pr=not args.no_pr)
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
