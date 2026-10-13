/**
 * DexterH4ck-ofertas — proxy de búsqueda para Google Apps Script.
 *
 * Fuentes activas sin configuración adicional:
 *   - Mercado Libre México (API pública de búsqueda)
 *   - Chedraui (catálogo VTEX público, sujeto a disponibilidad)
 *
 * Fuentes configurables mediante Propiedades del script:
 *   - WALMART_JSON_SEARCH_URL
 *   - BODEGA_JSON_SEARCH_URL
 *   - SORIANA_JSON_SEARCH_URL
 *
 * Cada URL configurable debe ser una API JSON autorizada y contener {q}.
 * Si la plantilla trae un marcador roto como {encodeURIComponent(termino)}, se
 * normaliza a {q} y el término se codifica una sola vez en el servidor.
 * No se intenta eludir CAPTCHA, WAF ni controles de acceso: si una tienda
 * configurable responde con bloqueo, HTTP de error o un JSON sin filas
 * reconocibles, se reporta como "not_configured" (con el campo "motivo") sin
 * afectar a las demás y sin contarla como éxito.
 */

const CFG = Object.freeze({
  minDescuento: 50,
  maxDescuento: 99,
  limite: 40,
  cacheSegundos: 240,
  terminos: ["liquidacion", "remate", "outlet"]
});

function doGet(e) {
  const params = (e && e.parameter) || {};
  const terminoManual = String(params.q || "").trim().slice(0, 100);
  const terminos = terminoManual ? [terminoManual] : CFG.terminos;
  const ofertas = [];
  const fuentes = [];
  const errores = [];

  const adaptadores = [
    { nombre: "MERCADO_LIBRE", buscar: buscarMercadoLibre },
    { nombre: "CHEDRAUI", buscar: buscarChedraui },
    { nombre: "WALMART", buscar: buscarWalmart },
    { nombre: "BODEGA_AURRERA", buscar: buscarBodega },
    { nombre: "SORIANA", buscar: buscarSoriana }
  ];

  adaptadores.forEach(adaptador => {
    let filas = 0;
    let consultasOk = 0;
    let consultasError = 0;
    let sinConfigurar = false;
    let motivo = "";

    terminos.forEach(termino => {
      try {
        const resultado = adaptador.buscar(termino);
        if (resultado === null) {
          consultasError++;
          return;
        }
        if (resultado && resultado.estado === "not_configured") {
          sinConfigurar = true;
          if (!motivo) motivo = String(resultado.motivo || "").slice(0, 120);
          return;
        }
        if (!Array.isArray(resultado)) {
          consultasError++;
          errores.push({
            tienda: adaptador.nombre,
            termino: termino,
            error: "Respuesta interna inesperada"
          });
          return;
        }
        consultasOk++;
        filas += resultado.length;
        ofertas.push.apply(ofertas, resultado);
      } catch (err) {
        consultasError++;
        errores.push({
          tienda: adaptador.nombre,
          termino: termino,
          error: String(err && err.message ? err.message : err).slice(0, 180)
        });
        console.warn(adaptador.nombre + ": " + String(err));
      }
    });

    let estado = "ok";
    if (consultasOk === 0 && sinConfigurar) estado = "not_configured";
    else if (consultasOk === 0) estado = "error";
    else if (consultasError > 0 || sinConfigurar) estado = "partial";

    const fuente = {
      tienda: adaptador.nombre,
      estado: estado,
      consultas_ok: consultasOk,
      consultas_error: consultasError,
      ofertas: filas
    };
    if (sinConfigurar && motivo) fuente.motivo = motivo;
    fuentes.push(fuente);
  });

  const unicas = deduplicar(ofertas);
  const exitosas = fuentes.filter(f => f.estado === "ok" || f.estado === "partial").length;
  const hayIncidencias = errores.length > 0 || fuentes.some(f => f.estado === "partial" || f.estado === "error");
  const status = exitosas ? (hayIncidencias ? "partial" : "success") : "error";

  return ContentService.createTextOutput(JSON.stringify({
    status: status,
    version: "2.2.0",
    generated_at: new Date().toISOString(),
    tiendas_monitoreadas: fuentes.map(f => f.tienda),
    total_detected: unicas.length,
    fuentes: fuentes,
    errores: errores,
    offers: unicas
  })).setMimeType(ContentService.MimeType.JSON);
}

function buscarMercadoLibre(termino) {
  const url = "https://api.mercadolibre.com/sites/MLM/search?q=" +
    encodeURIComponent(termino) + "&limit=" + CFG.limite;
  const json = pedirJSON(url, "ml:" + termino);
  if (!json || !Array.isArray(json.results)) return null;

  return json.results.map(p => normalizar({
    tienda: "MERCADO_LIBRE",
    id: p.id,
    nombre: p.title,
    actual: p.price,
    anterior: p.original_price,
    url: p.permalink,
    imagen: p.thumbnail
  })).filter(Boolean);
}

function buscarChedraui(termino) {
  const url = "https://www.chedraui.com.mx/api/catalog_system/pub/products/search" +
    "?ft=" + encodeURIComponent(termino) +
    "&_from=0&_to=" + (CFG.limite - 1);
  const json = pedirJSON(url, "chedraui:" + termino);
  if (!Array.isArray(json)) return null;

  return json.map(p => {
    const sku = p.items && p.items[0];
    const seller = sku && sku.sellers && sku.sellers[0];
    const offer = seller && seller.commertialOffer;
    if (!offer) return null;
    const imagen = sku.images && sku.images[0] ? sku.images[0].imageUrl : "";
    return normalizar({
      tienda: "CHEDRAUI",
      id: p.productId,
      nombre: p.productName,
      actual: offer.Price,
      anterior: offer.ListPrice,
      url: urlAbsoluta("https://www.chedraui.com.mx", p.link),
      imagen: imagen
    });
  }).filter(Boolean);
}

function buscarWalmart(termino) {
  return buscarConfigurada("WALMART_JSON_SEARCH_URL", "WALMART",
    "https://www.walmart.com.mx", termino);
}

function buscarBodega(termino) {
  return buscarConfigurada("BODEGA_JSON_SEARCH_URL", "BODEGA_AURRERA",
    "https://www.bodegaaurrera.com.mx", termino);
}

function buscarSoriana(termino) {
  return buscarConfigurada("SORIANA_JSON_SEARCH_URL", "SORIANA",
    "https://www.soriana.com", termino);
}

/**
 * Corrige marcadores rotos que se cuelan al copiar la URL a las Propiedades del
 * script, p. ej. {encodeURIComponent(termino)} o ${encodeURIComponent(termino)},
 * y los deja como {q}. El término se codifica después, una sola vez.
 */
function normalizarPlantilla(plantilla) {
  return String(plantilla || "").trim()
    .replace(/\$?\{\s*encodeURIComponent\(\s*\w+\s*\)\s*\}/g, "{q}")
    .replace(/%7Bq%7D/gi, "{q}");
}

function buscarConfigurada(propiedad, tienda, base, termino) {
  const cruda = PropertiesService.getScriptProperties().getProperty(propiedad);
  if (!cruda) return { estado: "not_configured", motivo: "propiedad_ausente" };
  const plantilla = normalizarPlantilla(cruda);
  if (plantilla.indexOf("{q}") < 0 || !/^https:\/\//i.test(plantilla)) {
    return { estado: "not_configured", motivo: "plantilla_invalida" };
  }

  const url = plantilla.replace(/\{q\}/g, encodeURIComponent(termino));
  let json;
  try {
    json = pedirJSON(url, tienda.toLowerCase() + ":" + termino);
  } catch (err) {
    // WAF, endpoint cambiado o respuesta que no es JSON: se aísla solo esta tienda,
    // sin romper el flujo general ni reportar un éxito falso.
    const motivo = String(err && err.message ? err.message : err).slice(0, 120);
    console.warn(tienda + ": " + motivo);
    return { estado: "not_configured", motivo: motivo };
  }
  if (!json) return { estado: "not_configured", motivo: "json_vacio" };

  let productos = [];
  if (Array.isArray(json)) productos = json;
  else if (json.products && Array.isArray(json.products.items)) productos = json.products.items;
  else if (Array.isArray(json.results)) productos = json.results;
  else if (Array.isArray(json.products)) productos = json.products;
  else return { estado: "not_configured", motivo: "json_sin_filas_reconocibles" };

  const baseSeguro = base;
  return productos.map(p => {
    const seller = p.items && p.items[0] && p.items[0].sellers &&
      p.items[0].sellers[0];
    const vtex = seller && seller.commertialOffer;
    const actual = vtex ? vtex.Price :
      (p.priceInfo && p.priceInfo.currentPrice
        ? p.priceInfo.currentPrice.price
        : (p.price !== undefined ? p.price : p.salePrice));
    const anterior = vtex ? vtex.ListPrice :
      (p.priceInfo && p.priceInfo.wasPrice
        ? p.priceInfo.wasPrice.price
        : (p.original_price !== undefined ? p.original_price :
          (p.listPrice !== undefined ? p.listPrice : p.regularPrice)));
    const ruta = p.canonicalUrl || p.link || p.permalink || p.url || "";
    const imagen = p.imageInfo && p.imageInfo.thumbnailUrl
      ? p.imageInfo.thumbnailUrl
      : (p.thumbnail || (p.items && p.items[0] && p.items[0].images &&
        p.items[0].images[0] ? p.items[0].images[0].imageUrl : ""));
    return normalizar({
      tienda: tienda,
      id: p.id || p.productId || p.sku,
      nombre: p.name || p.title || p.productName,
      actual: actual,
      anterior: anterior,
      url: urlAbsoluta(baseSeguro, ruta),
      imagen: imagen
    });
  }).filter(Boolean);
}

function pedirJSON(url, cacheKey) {
  if (!/^https:\/\/[a-z0-9.-]+(?:\/|$)/i.test(url)) {
    throw new Error("URL HTTPS inválida");
  }

  const cache = CacheService.getScriptCache();
  const key = "dexter-proxy-v2:" + cacheKey;
  const cached = cache.get(key);
  if (cached) {
    try { return JSON.parse(cached); } catch (ignored) { cache.remove(key); }
  }

  const response = UrlFetchApp.fetch(url, {
    method: "get",
    muteHttpExceptions: true,
    followRedirects: true,
    headers: { Accept: "application/json" }
  });
  const code = response.getResponseCode();
  if (code < 200 || code >= 300) {
    // Propagar el código al JSON de diagnóstico para distinguir 403, 404, 429 y 5xx.
    console.warn("HTTP " + code + " en fuente " + cacheKey);
    throw new Error("HTTP " + code + " en fuente " + cacheKey);
  }

  const body = response.getContentText();
  let data;
  try {
    data = JSON.parse(body);
  } catch (err) {
    console.warn("Respuesta no JSON en fuente " + cacheKey);
    throw new Error("Respuesta no JSON en fuente " + cacheKey);
  }

  // Apps Script limita el tamaño de cada valor de caché.
  if (body.length < 80000) {
    try { cache.put(key, body, CFG.cacheSegundos); } catch (ignored) {}
  }
  return data;
}


function urlAbsoluta(base, ruta) {
  const valor = String(ruta || "").trim();
  if (!valor) return "";
  if (/^https?:\/\//i.test(valor)) return valor;
  return base.replace(/\/$/, "") + "/" + valor.replace(/^\//, "");
}

// Unidades de medida estándar: un número pegado a una de ellas ("875g", "1 kg",
// "400 ml") es un gramaje del título, nunca un precio.
const UNIDADES_MEDIDA = "kg|kgs|kilos?|kilogramos?|gr|grs|gramos?|g|mg|ml|mls|cc|lt|lts|litros?|l|" +
  "oz|onzas?|lb|lbs|libras?|pzas?|pzs?|pz|piezas?|pack|paquetes?|" +
  "cm|mm|mts?|metros?|m|gb|tb|mb|mah|w|watts?|v|hz|pulgadas?|pulg|in|rollos?|hojas?|capsulas?|cápsulas?|tabletas?";

function medidasDelTitulo(texto) {
  const medidas = [];
  const re = new RegExp("(^|[^\\w.$,])(\\d+(?:[.,]\\d+)?)\\s*(" + UNIDADES_MEDIDA +
    ")(?![A-Za-zÁÉÍÓÚáéíóúÑñ])", "gi");
  const cadena = String(texto || "");
  let m;
  while ((m = re.exec(cadena)) !== null) {
    const n = parseFloat(m[2].replace(",", "."));
    if (!Number.isFinite(n)) continue;
    medidas.push(n);
    if (/^(kg|kgs|kilos?|kilogramos?|l|lt|lts|litros?)$/i.test(m[3])) medidas.push(n * 1000);
  }
  return medidas;
}

function normalizar(p) {
  const actual = Number(p.actual);
  const anterior = Number(p.anterior);
  const id = p.id;
  const nombre = String(p.nombre || "").trim();
  const url = String(p.url || "").trim();

  if (id === undefined || id === null || !nombre || !url) return null;
  if (!Number.isFinite(actual) || !Number.isFinite(anterior)) return null;
  if (actual <= 0 || anterior <= actual) return null;
  if (!/^https?:\/\//i.test(url)) return null;

  const descuento = Math.round((1 - actual / anterior) * 100);
  if (descuento < CFG.minDescuento || descuento > CFG.maxDescuento) return null;
  // Anti-bug de gramajes: si el "precio anterior" coincide con una medida del título
  // (875 de "875g", 1000 de "1 kg"), es un nodo contaminado y fabricaría un descuento falso.
  if (medidasDelTitulo(nombre).some(medida => Math.abs(anterior - medida) < 0.005)) return null;

  return {
    tienda: p.tienda,
    id: String(id),
    nombre: nombre.slice(0, 180),
    precio_anterior: anterior,
    precio_actual: actual,
    descuento: descuento,
    url: url,
    imagen: String(p.imagen || "")
  };
}

function deduplicar(ofertas) {
  const vistos = {};
  return ofertas.filter(p => {
    const clave = p.tienda + ":" + (p.id || p.url);
    if (vistos[clave]) return false;
    vistos[clave] = true;
    return true;
  });
}

/**
 * Prueba manual de una búsqueda individual. Selecciona testProxy en el editor
 * y revisa el registro; no envía mensajes a Telegram.
 */
function testProxy() {
  const resultado = doGet({ parameter: { q: "liquidacion" } });
  Logger.log(resultado.getContent());
}
