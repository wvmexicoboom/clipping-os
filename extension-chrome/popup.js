/* Clipping OS — popup de captura.
 *
 * Alcance deliberado: solo LECTURA de lo que ya está visible en la página y
 * almacenamiento local. No hay clicks sintéticos, ni inyección en formularios
 * de redes sociales, ni rotación de sesiones.
 *
 * El CSV que exporta se carga en el pipeline con:
 *     python main.py importar --csv campanas.csv
 */

const CAMPOS = ["url", "marca", "categoria", "cpm", "total", "rest", "minviews", "cap", "plataformas", "reglas"];
const $ = (id) => document.getElementById(id);
const estado = (msg, ok = true) => {
  const e = $("estado");
  e.textContent = msg;
  e.style.color = ok ? "var(--ok)" : "var(--bad)";
};

async function paginaActual() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab || {};
}

/* Heurística de lectura: busca el texto visible que rodea a las etiquetas típicas
 * de una tarjeta de campaña. Si la plataforma cambia su markup, esto devuelve null
 * en lugar de inventar un número: mejor campo vacío que dato falso. */
function extraer(texto) {
  if (!texto) return {};
  const num = (re) => {
    const m = texto.match(re);
    return m ? parseFloat(m[1].replace(/,/g, "")) : null;
  };
  return {
    cpm: num(/\$\s?([\d.]+)\s*(?:\/|per|por)\s*1?,?000\s*(?:views|vistas)/i),
    total: num(/(?:total|budget|presupuesto)[^\d$]{0,20}\$\s?([\d,.]+[kKmM]?)/i),
    rest: num(/(?:remaining|restante|left)[^\d$]{0,20}\$\s?([\d,.]+[kKmM]?)/i),
    minviews: num(/(?:min(?:imo)?)[^\d]{0,20}([\d,.]+)\s*(?:views|vistas)/i),
    cap: num(/(?:cap|tope|max(?:imo)?)[^\d$]{0,20}\$\s?([\d,.]+)/i),
  };
}

function normalizar(v) {
  if (v === null || v === undefined || v === "") return "";
  const s = String(v).toLowerCase();
  const mult = s.endsWith("k") ? 1e3 : s.endsWith("m") ? 1e6 : 1;
  const n = parseFloat(s.replace(/[^0-9.]/g, ""));
  return Number.isFinite(n) ? String(n * mult) : String(v);
}

async function capturar() {
  const tab = await paginaActual();
  if (!tab.url) return estado("No hay pestaña activa.", false);
  $("url").value = tab.url;
  if (!tab.id) return;

  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.body ? document.body.innerText.slice(0, 20000) : "",
    });
    const d = extraer(result);
    if (d.cpm != null) $("cpm").value = d.cpm;
    if (d.total != null) $("total").value = normalizar(d.total);
    if (d.rest != null) $("rest").value = normalizar(d.rest);
    if (d.minviews != null) $("minviews").value = d.minviews;
    if (d.cap != null) $("cap").value = normalizar(d.cap);
    if (!$("marca").value) $("marca").value = (tab.title || "").split(/[|\-–]/)[0].trim();

    const leidos = Object.values(d).filter((v) => v != null).length;
    estado(leidos
      ? `Leídos ${leidos} campo(s) de la página. Revisa y completa a mano.`
      : "No reconocí el formato de la página: llena los campos a mano.", leidos > 0);
  } catch (e) {
    estado("No pude leer la página (" + e.message + "). Llena los campos a mano.", false);
  }
}

function leerFormulario() {
  const fila = { requiere_waitlist: 0 };
  for (const c of CAMPOS) fila[c === "cpm" ? "cpm_usd" : c === "total" ? "presupuesto_total"
    : c === "rest" ? "presupuesto_rest" : c === "minviews" ? "min_views"
    : c === "cap" ? "cap_por_clip_usd" : c === "reglas" ? "reglas_texto" : c] = $(c).value.trim();
  fila.titulo = document.title || "";
  return fila;
}

async function guardar() {
  const fila = leerFormulario();
  if (!fila.url) return estado("Falta la URL: sin ella no hay clave única.", false);
  const { campanas = [] } = await chrome.storage.local.get("campanas");
  const i = campanas.findIndex((c) => c.url === fila.url);
  fila.capturado_en = new Date().toISOString();
  if (i >= 0) campanas[i] = { ...campanas[i], ...fila }; else campanas.push(fila);
  await chrome.storage.local.set({ campanas });
  estado(`Guardada. ${campanas.length} campaña(s) en memoria local.`);
}

function csv(campanas) {
  const cols = ["url", "titulo", "marca", "categoria", "cpm_usd", "presupuesto_total",
                "presupuesto_rest", "plataformas_ok", "min_views", "cap_por_clip_usd",
                "requiere_waitlist", "reglas_texto"];
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""').replace(/\r?\n/g, " ")}"`;
  return [cols.join(","),
          ...campanas.map((c) => cols.map((k) => esc(c[k])).join(","))].join("\n");
}

async function exportar() {
  const { campanas = [] } = await chrome.storage.local.get("campanas");
  if (!campanas.length) return estado("No hay campañas guardadas todavía.", false);
  const blob = new Blob(["\ufeff" + csv(campanas)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  await chrome.downloads.download({
    url, filename: "campanas.csv", saveAs: true,
  });
  URL.revokeObjectURL(url);
  estado(`Exportadas ${campanas.length} campaña(s). Ahora: python main.py importar --csv campanas.csv`);
}

(async () => {
  const tab = await paginaActual();
  if (tab.url) $("url").value = tab.url;
  const { campanas = [] } = await chrome.storage.local.get("campanas");
  if (campanas.length) estado(`${campanas.length} campaña(s) guardadas en esta máquina.`);
})();

$("capturar").addEventListener("click", capturar);
$("guardar").addEventListener("click", guardar);
$("exportar").addEventListener("click", exportar);
