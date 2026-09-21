// Parva — front vanilla (sin build). Consume la API del backend.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtMoney = (n) => (n == null ? '—' : new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 }).format(n));
const api = (url, opts) => fetch(url, opts).then((r) => r.json());

let current = null;         // { productor, ... } estado actual
let productorActivo = null; // id elegido en el selector
let ultimoEstado = '';      // serializado del último estado pintado
let secuenciaRefresco = 0;
let ultimoRefrescoAplicado = 0;

// Los datos entran por WhatsApp desde el celular del productor, no desde esta
// página: sin un refresco propio el dashboard queda congelado hasta un F5.
const REFRESH_MS = 5000;
let timerRefresco = null;

// ---- Carga inicial ----
async function init() {
  const productores = await api('/api/productores');
  $('selProductor').innerHTML = productores
    .map((p) => `<option value="${p.id}">${esc(p.nombre)}</option>`).join('');
  $('selProductor').onchange = () => selectProductor(Number($('selProductor').value));
  $('btnExport').onclick = () => window.open(`/api/export?productorId=${current.productor.id}&sheet=movimientos`, '_blank');
  await selectProductor(productores[0].id);
  autoRefresco();
}

async function selectProductor(pid) {
  productorActivo = pid;
  // Invalida las peticiones de la selección anterior, incluso si se vuelve al
  // mismo productor antes de que terminen.
  ultimoRefrescoAplicado = ++secuenciaRefresco;
  await refrescar();
}

/**
 * Pinta el estado sólo si cambió.
 *
 * Con la pestaña abierta esto corre cada 5 s, y un innerHTML por tick le
 * voltearía el scroll y la selección de texto a alguien que está leyendo la
 * planilla. El id del productor viaja adentro del estado, así que cambiar de
 * productor siempre difiere y repinta.
 */
function aplicarEstado(state) {
  // Un refresco del productor anterior puede seguir en vuelo cuando se cambia
  // de productor y contestar DESPUÉS: sin este guard la página vuelve sola al
  // productor viejo y se queda ahí, porque los próximos ticks lo siguen a él.
  if (state?.productor?.id !== productorActivo) return;

  // Sólo estas colecciones alimentan la vista. Una consulta del bot cambia
  // `mensajes` sin cambiar la planilla y debe conservar el DOM y su selección.
  const { productor, movimientos, hacienda, lotes, sanidad, margenes } = state;
  const snapshot = JSON.stringify({ productor, movimientos, hacienda, lotes, sanidad, margenes });
  current = state;
  if (snapshot === ultimoEstado) return;
  ultimoEstado = snapshot;
  renderDashboard();
}

async function refrescar() {
  if (productorActivo == null) return;
  const solicitud = ++secuenciaRefresco;
  try {
    const state = await api(`/api/state?productorId=${productorActivo}`);
    // Una petición lenta puede aplicarse mientras no haya una respuesta más
    // reciente: así el dashboard también avanza cuando la red tarda más de 5 s.
    if (solicitud < ultimoRefrescoAplicado) return;
    aplicarEstado(state);
    ultimoRefrescoAplicado = solicitud;
  } catch {
    // Una falla puntual (server reiniciando, red) no rompe nada: el próximo
    // tick reintenta y mientras tanto queda a la vista lo último bueno.
  }
}

// Sólo consulta con la pestaña visible: una pestaña de fondo no le sirve a
// nadie y son 12 requests por minuto. Al volver refresca de una, para no
// quedarse mirando datos viejos hasta que caiga el tick.
function autoRefresco() {
  const arrancar = () => { timerRefresco ??= setInterval(refrescar, REFRESH_MS); };
  const parar = () => { clearInterval(timerRefresco); timerRefresco = null; };
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return parar();
    refrescar();
    arrancar();
  });
  if (!document.hidden) arrancar();
}

// ---- Render dashboard ----
function renderDashboard() {
  const { productor: p, movimientos, hacienda, lotes } = current;
  $('variante').textContent = p.tipo_campo;

  const gasto = movimientos.filter((m) => ['insumo', 'labor', 'gasto'].includes(m.tipo)).reduce((a, m) => a + (m.monto || 0), 0);
  const venta = movimientos.filter((m) => m.tipo === 'venta').reduce((a, m) => a + (m.monto || 0), 0);
  const cabezas = hacienda.reduce((a, h) => a + h.cantidad, 0);
  const ha = lotes.reduce((a, l) => a + (l.hectareas || 0), 0);

  // La variante define qué paneles se muestran por default, pero si hay datos de
  // la otra actividad hay que mostrarlos igual. El bot no mira tipo_campo: un
  // productor agrícola que carga hacienda por WhatsApp recibía el ✅ y no veía
  // ningún cambio en la página (el dato estaba guardado, el panel no se dibujaba).
  const verAgro = p.tipo_campo !== 'ganadero' || lotes.length > 0;
  const verHacienda = p.tipo_campo !== 'agricola' || hacienda.length > 0 || current.sanidad.length > 0;

  const kpis = [['Movimientos', movimientos.length], ['Gastos', fmtMoney(gasto)], ['Ventas', fmtMoney(venta)]];
  if (verAgro) kpis.push(['Hectáreas', `${ha} ha`]);
  if (verHacienda) kpis.push(['Hacienda', `${cabezas} cabezas`]);
  $('kpis').innerHTML = kpis.map(([l, v]) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${l}</div></div>`).join('');

  let html = panelPlanilla();
  if (verAgro) html += panelMargenes();
  if (verHacienda) html += panelHacienda() + panelSanidad();
  $('panels').innerHTML = html;
}

function panelPlanilla() {
  const rows = current.movimientos.map((m) => `
    <tr>
      <td>${esc(m.fecha)}</td>
      <td><span class="tag ${esc(m.tipo)}">${esc(m.tipo)}</span></td>
      <td>${esc(m.lote_nombre ?? '—')}</td>
      <td>${esc(m.producto ?? '')} ${m.cantidad != null ? esc(m.cantidad) + ' ' + esc(m.unidad ?? '') : ''}</td>
      <td class="num">${m.monto != null ? fmtMoney(m.monto) : '—'}</td>
      <td><span class="tag ${m.origen === 'bot' ? 'bot' : ''}">${esc(m.origen)}</span></td>
    </tr>`).join('');
  return `<div class="panel"><h2>Planilla · gestión diaria</h2><div class="panel-body" style="padding:0">
    <table><thead><tr><th>Fecha</th><th>Tipo</th><th>Lote</th><th>Detalle</th><th class="num">Monto</th><th>Origen</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="empty">Sin movimientos.</td></tr>'}</tbody></table></div></div>`;
}

function panelMargenes() {
  const cards = current.margenes.map((m) => {
    if (!m.confiable) {
      return `<div class="card no-confiable"><div class="lote">${esc(m.loteNombre)}</div><div class="uso">${esc(m.uso ?? '')}</div>
        <div class="margen">Margen no disponible</div>
        <div class="alerta">⚠ ${esc(m.razon)} · costos ${fmtMoney(m.costos)}</div></div>`;
    }
    return `<div class="card"><div class="lote">${esc(m.loteNombre)}</div><div class="uso">${esc(m.uso ?? '')}</div>
      <div class="margen">${fmtMoney(m.margen)}</div>
      <div class="desglose">ventas ${fmtMoney(m.ventas)} − costos ${fmtMoney(m.costos)}</div></div>`;
  }).join('');
  return `<div class="panel"><h2>Lotes y márgenes</h2><div class="panel-body"><div class="grid-cards">${cards || '<div class="empty">Sin lotes.</div>'}</div></div></div>`;
}

function panelHacienda() {
  const total = current.hacienda.reduce((a, h) => a + h.cantidad, 0);
  const rows = current.hacienda.map((h) => `<tr><td>${esc(h.categoria)}</td><td class="num">${esc(h.cantidad)}</td></tr>`).join('');
  return `<div class="panel"><h2>Hacienda · stock actual (${total} cabezas)</h2><div class="panel-body" style="padding:0">
    <table><thead><tr><th>Categoría</th><th class="num">Cantidad</th></tr></thead><tbody>${rows || '<tr><td colspan="2" class="empty">Sin hacienda.</td></tr>'}</tbody></table></div></div>`;
}

function panelSanidad() {
  const rows = current.sanidad.map((s) => `<tr><td>${esc(s.fecha)}</td><td>${esc(s.producto ?? '')}</td><td>${esc(s.categoria ?? '')}</td><td class="num">${esc(s.cantidad ?? '')}</td></tr>`).join('');
  return `<div class="panel"><h2>Sanidad</h2><div class="panel-body" style="padding:0">
    <table><thead><tr><th>Fecha</th><th>Producto</th><th>Categoría</th><th class="num">Cantidad</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="empty">Sin registros.</td></tr>'}</tbody></table></div></div>`;
}

init();
