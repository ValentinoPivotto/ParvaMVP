// Parva MVP — front vanilla (sin build). Consume la API del backend.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtMoney = (n) => (n == null ? '—' : new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 }).format(n));
const api = (url, opts) => fetch(url, opts).then((r) => r.json());

let current = null;       // { productor, ... } estado actual
let currentTel = null;    // teléfono del usuario seleccionado en el simulador

const CHIPS = {
  agricola: [
    'Compré 200 litros de gasoil para el lote 4',
    'Pagué $600.000 de fumigación en el lote 4',
    'Vendí 240 tn de soja por $9.600.000',
    '¿Cuál es el margen del lote 1?',
  ],
  ganadero: [
    'Nacieron 8 terneros',
    'Se murieron 2 vacas',
    '¿Cuántos terneros tengo?',
    'Vacuné 120 vacas contra la aftosa',
  ],
  mixto: [
    'Compré 200 litros de gasoil para el lote 4',
    'Nacieron 8 terneros',
    '¿Cuál es el margen del lote 1?',
    '¿Cuántos terneros tengo?',
  ],
};

// ---- Carga inicial ----
async function init() {
  const productores = await api('/api/productores');
  $('selProductor').innerHTML = productores
    .map((p) => `<option value="${p.id}">${esc(p.nombre)}</option>`).join('');
  $('selProductor').onchange = () => selectProductor(Number($('selProductor').value));
  $('btnExport').onclick = () => window.open(`/api/export?productorId=${current.productor.id}&sheet=movimientos`, '_blank');
  $('selUsuario').onchange = () => { currentTel = $('selUsuario').value; };
  $('formMsg').onsubmit = onSend;
  await selectProductor(productores[0].id);
}

async function selectProductor(pid) {
  current = await api(`/api/state?productorId=${pid}`);
  renderUsuarios();
  renderChips();
  renderDashboard();
  resetChat();
}

async function refreshDashboard() {
  current = await api(`/api/state?productorId=${current.productor.id}`);
  renderDashboard();
}

// ---- Render dashboard ----
function renderDashboard() {
  const { productor: p, movimientos, hacienda, lotes } = current;
  $('variante').textContent = p.tipo_campo;

  const gasto = movimientos.filter((m) => ['insumo', 'labor', 'gasto'].includes(m.tipo)).reduce((a, m) => a + (m.monto || 0), 0);
  const venta = movimientos.filter((m) => m.tipo === 'venta').reduce((a, m) => a + (m.monto || 0), 0);
  const cabezas = hacienda.reduce((a, h) => a + h.cantidad, 0);
  const ha = lotes.reduce((a, l) => a + (l.hectareas || 0), 0);

  const kpis = [['Movimientos', movimientos.length], ['Gastos', fmtMoney(gasto)], ['Ventas', fmtMoney(venta)]];
  if (p.tipo_campo !== 'ganadero') kpis.push(['Hectáreas', `${ha} ha`]);
  if (p.tipo_campo !== 'agricola') kpis.push(['Hacienda', `${cabezas} cabezas`]);
  $('kpis').innerHTML = kpis.map(([l, v]) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${l}</div></div>`).join('');

  let html = panelPlanilla();
  if (p.tipo_campo !== 'ganadero') html += panelMargenes();
  if (p.tipo_campo !== 'agricola') html += panelHacienda() + panelSanidad();
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

// ---- Simulador ----
function renderUsuarios() {
  const us = current.usuarios;
  $('selUsuario').innerHTML = us.map((u) => `<option value="${esc(u.telefono)}">${esc(u.nombre)} · ${esc(u.rol)}</option>`).join('');
  currentTel = us[0]?.telefono ?? null;
}

function renderChips() {
  const chips = CHIPS[current.productor.tipo_campo] ?? CHIPS.mixto;
  $('chips').innerHTML = chips.map((c) => `<button class="chip">${esc(c)}</button>`).join('');
  [...$('chips').querySelectorAll('.chip')].forEach((b) => {
    b.onclick = () => { $('inputMsg').value = b.textContent; $('inputMsg').focus(); };
  });
}

function resetChat() {
  $('chat').innerHTML = '';
  addBubble('in', `¡Hola ${current.usuarios[0]?.nombre ?? ''}! Soy Parva 🌾. Contame qué pasó en el campo o preguntame algo.`);
}

function addBubble(dir, text, meta) {
  const div = document.createElement('div');
  div.className = `bubble ${dir === 'out' ? 'out' : 'in'}` + (meta && meta.denied ? ' denied' : '');
  div.innerHTML = esc(text) + (meta && meta.label ? `<div class="meta">${esc(meta.label)}</div>` : '');
  $('chat').appendChild(div);
  $('chat').scrollTop = $('chat').scrollHeight;
}

async function onSend(e) {
  e.preventDefault();
  const texto = $('inputMsg').value.trim();
  if (!texto || !currentTel) return;
  $('inputMsg').value = '';
  addBubble('out', texto);
  try {
    const res = await api('/api/whatsapp/sim', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telefono: currentTel, texto }),
    });
    const label = `${res.status} · confianza ${(res.confidence ?? 0).toFixed(2)}`;
    addBubble('in', res.reply ?? res.error ?? '...', { label, denied: res.status === 'denied' });
    await refreshDashboard();
  } catch (err) {
    addBubble('in', 'Error de conexión con el backend.');
  }
}

init();
