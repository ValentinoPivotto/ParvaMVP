import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../frontend/app.js', import.meta.url), 'utf8');
const flush = () => new Promise((resolve) => setImmediate(resolve));

function estado(productorId = 1, cantidad = 20) {
  return {
    productor: { id: productorId, nombre: `Productor ${productorId}`, tipo_campo: 'mixto' },
    movimientos: [], lotes: [], sanidad: [], margenes: [],
    hacienda: [{ categoria: 'ternero', cantidad }],
    campos: [], campanias: [], mensajes: [],
  };
}

// Ejecuta app.js completo, con respuestas controladas para reproducir carreras
// sin temporizadores reales ni dependencias de navegador.
async function abrirDashboard() {
  const elementos = new Map();
  const eventos = new Map();
  const intervalos = new Map();
  const solicitudes = [];
  let proximoIntervalo = 0;
  const document = {
    hidden: false,
    getElementById(id) {
      if (!elementos.has(id)) elementos.set(id, {
        html: '', escrituras: 0, textContent: '', value: '1',
        set innerHTML(value) { this.html = value; this.escrituras++; },
        get innerHTML() { return this.html; },
      });
      return elementos.get(id);
    },
    addEventListener(evento, callback) { eventos.set(evento, callback); },
  };
  const listo = vm.runInNewContext(source, {
    document,
    window: { open() {} },
    fetch: (url) => new Promise((resolve, reject) => solicitudes.push({
      url, resolve: (body) => resolve({ json: async () => body }), reject,
    })),
    setInterval(callback, ms) {
      assert.equal(ms, 5000);
      intervalos.set(++proximoIntervalo, callback);
      return proximoIntervalo;
    },
    clearInterval(id) { intervalos.delete(id); },
  });
  function solicitud(url) {
    const siguiente = solicitudes.shift();
    assert.equal(siguiente?.url, url);
    return siguiente;
  }
  solicitud('/api/productores').resolve([estado(1).productor, estado(2).productor]);
  await flush();
  solicitud('/api/state?productorId=1').resolve(estado());
  await listo;

  return {
    elemento: (id) => document.getElementById(id),
    solicitud,
    refrescar: () => {
      assert.equal(intervalos.size, 1);
      return [...intervalos.values()][0]();
    },
    seleccionar: (id) => {
      const selector = document.getElementById('selProductor');
      selector.value = String(id);
      return selector.onchange();
    },
    visibilidad: (visible) => {
      document.hidden = !visible;
      eventos.get('visibilitychange')();
    },
    intervalos, solicitudes,
  };
}

test('una respuesta vieja no reemplaza el stock de una respuesta más reciente', async () => {
  const app = await abrirDashboard();
  const anterior = app.refrescar();
  const respuestaAnterior = app.solicitud('/api/state?productorId=1');
  const nueva = app.refrescar();
  app.solicitud('/api/state?productorId=1').resolve(estado(1, 25));
  await nueva;
  respuestaAnterior.resolve(estado(1, 20));
  await anterior;
  assert.match(app.elemento('kpis').innerHTML, /25 cabezas/);
  assert.equal(app.elemento('panels').escrituras, 2);
});

test('una respuesta válida se muestra aunque haya otro refresco pendiente', async () => {
  const app = await abrirDashboard();
  const anterior = app.refrescar();
  const respuestaAnterior = app.solicitud('/api/state?productorId=1');
  const nueva = app.refrescar();
  const respuestaNueva = app.solicitud('/api/state?productorId=1');
  respuestaAnterior.resolve(estado(1, 25));
  await anterior;
  assert.match(app.elemento('kpis').innerHTML, /25 cabezas/);
  respuestaNueva.resolve(estado(1, 30));
  await nueva;
  assert.match(app.elemento('kpis').innerHTML, /30 cabezas/);
});

test('cambiar de productor invalida respuestas anteriores incluso al volver al mismo', async () => {
  const app = await abrirDashboard();
  const anterior = app.refrescar();
  const respuestaAnterior = app.solicitud('/api/state?productorId=1');
  const cambio = app.seleccionar(2);
  const respuestaCambio = app.solicitud('/api/state?productorId=2');
  const vuelta = app.seleccionar(1);
  const respuestaVuelta = app.solicitud('/api/state?productorId=1');
  respuestaAnterior.resolve(estado(1, 15));
  await anterior;
  assert.match(app.elemento('kpis').innerHTML, /20 cabezas/);
  respuestaVuelta.resolve(estado(1, 30));
  await vuelta;
  respuestaCambio.resolve(estado(2, 99));
  await cambio;
  assert.match(app.elemento('kpis').innerHTML, /30 cabezas/);
});

test('un estado reciente sin cambios también impide aplicar una respuesta vieja', async () => {
  const app = await abrirDashboard();
  const anterior = app.refrescar();
  const respuestaAnterior = app.solicitud('/api/state?productorId=1');
  const nueva = app.refrescar();
  app.solicitud('/api/state?productorId=1').resolve(estado());
  await nueva;
  respuestaAnterior.resolve(estado(1, 15));
  await anterior;
  assert.match(app.elemento('kpis').innerHTML, /20 cabezas/);
  assert.equal(app.elemento('panels').escrituras, 1);
});

test('las consultas de WhatsApp y otros datos ajenos a la vista conservan el DOM', async () => {
  const app = await abrirDashboard();
  const siguiente = estado();
  siguiente.mensajes = [{ id: 1, texto_original: '¿Cuántos terneros tengo?', estado: 'confirmed' }];
  siguiente.campos = [{ id: 1, nombre: 'Campo Norte' }];
  siguiente.campanias = [{ id: 1, nombre: '2026/27' }];
  const refresco = app.refrescar();
  app.solicitud('/api/state?productorId=1').resolve(siguiente);
  await refresco;
  assert.equal(app.elemento('panels').escrituras, 1);
  assert.equal(app.elemento('kpis').escrituras, 1);
});

test('los cambios visibles siguen actualizando los KPIs y paneles', async () => {
  const app = await abrirDashboard();
  const siguiente = estado(1, 25);
  siguiente.movimientos = [{ tipo: 'insumo', fecha: '2026-09-21', producto: 'Gasoil', cantidad: 200, unidad: 'L', monto: 100, origen: 'bot' }];
  siguiente.lotes = [{ id: 1, hectareas: 10 }];
  siguiente.sanidad = [{ fecha: '2026-09-21', producto: 'Vacuna aftosa', categoria: 'ternero', cantidad: 25 }];
  siguiente.margenes = [{ loteNombre: 'Lote 1', uso: 'Soja', confiable: true, margen: 900, ventas: 1000, costos: 100 }];
  const refresco = app.refrescar();
  app.solicitud('/api/state?productorId=1').resolve(siguiente);
  await refresco;
  assert.match(app.elemento('kpis').innerHTML, /25 cabezas/);
  assert.match(app.elemento('kpis').innerHTML, /10 ha/);
  assert.match(app.elemento('panels').innerHTML, /Gasoil/);
  assert.match(app.elemento('panels').innerHTML, /Vacuna aftosa/);
  assert.match(app.elemento('panels').innerHTML, /Lote 1/);
  assert.equal(app.elemento('panels').escrituras, 2);
});

test('una falla de red conserva los datos y permite el siguiente refresco', async () => {
  const app = await abrirDashboard();
  const fallido = app.refrescar();
  app.solicitud('/api/state?productorId=1').reject(new Error('sin conexión'));
  await fallido;
  assert.equal(app.elemento('panels').escrituras, 1);
  const siguiente = app.refrescar();
  app.solicitud('/api/state?productorId=1').resolve(estado(1, 25));
  await siguiente;
  assert.match(app.elemento('kpis').innerHTML, /25 cabezas/);
});

test('la pestaña oculta pausa el intervalo y al volver se refresca inmediatamente', async () => {
  const app = await abrirDashboard();
  app.visibilidad(false);
  assert.equal(app.intervalos.size, 0);
  assert.equal(app.solicitudes.length, 0);
  app.visibilidad(true);
  assert.equal(app.intervalos.size, 1);
  app.solicitud('/api/state?productorId=1').resolve(estado(1, 25));
  await flush();
  assert.match(app.elemento('kpis').innerHTML, /25 cabezas/);
});
