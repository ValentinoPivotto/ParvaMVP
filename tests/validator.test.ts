// Tests del guard de hacienda. Es lógica determinística y sin red, así que a
// diferencia del eval sí puede correr sola y en cada cambio.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validate } from '../src/pipeline/validator.ts';
import type { Normalized } from '../src/pipeline/normalizer.ts';

function normalizado(extra: Partial<Normalized>): Normalized {
  return { recordType: 'venta', loteId: null, loteResuelto: true, fecha: '2026-09-15', ...extra } as Normalized;
}

test('bloquea una venta de animales tipada como movimiento', () => {
  const v = validate(normalizado({ recordType: 'venta', producto: 'novillo', cantidad: 30, monto: 1200000 }), 'owner', 0.95);
  assert.equal(v.ok, false);
  assert.equal(v.reformular, true, 'tiene que impedir el "¿lo registro igual?": confirmarlo guardaría mal el registro');
  assert.match(v.motivo, /novillo/);
});

test('bloquea también compras de animales tipadas como insumo', () => {
  const v = validate(normalizado({ recordType: 'insumo', producto: 'vaquillonas', cantidad: 25 }), 'owner', 0.95);
  assert.equal(v.reformular, true);
});

test('no toca una venta de grano', () => {
  const v = validate(normalizado({ recordType: 'venta', producto: 'soja', monto: 4200000 }), 'owner', 0.95);
  assert.equal(v.ok, true);
  assert.notEqual(v.reformular, true);
});

test('no toca un insumo destinado a animales', () => {
  // "compré 500 kg de ración para las vacas": la categoría es animal pero lo
  // que se transa es ración. Mirar `producto` y no la descripción es lo que
  // evita este falso positivo.
  const v = validate(normalizado({ recordType: 'insumo', producto: 'ración', categoria: 'vaca', cantidad: 500 }), 'owner', 0.95);
  assert.equal(v.ok, true);
  assert.notEqual(v.reformular, true);
});

test('no toca sanidad aplicada a animales', () => {
  const v = validate(normalizado({ recordType: 'evento_sanitario', producto: 'ivermectina', categoria: 'vaca' }), 'owner', 0.95);
  assert.equal(v.ok, true);
});

test('no toca hacienda bien tipada', () => {
  const v = validate(normalizado({ recordType: 'evento_hacienda', categoria: 'novillo', eventoTipo: 'venta', cantidad: 30 }), 'owner', 0.95);
  assert.equal(v.ok, true);
});

test('el guard corre antes que los campos requeridos', () => {
  // Una venta de animales sin monto fallaría igual por "falta el monto"; el
  // mensaje útil es el del guard, no el genérico.
  const v = validate(normalizado({ recordType: 'venta', producto: 'terneros', cantidad: 8 }), 'owner', 0.95);
  assert.equal(v.reformular, true);
});

test('el permiso por rol sigue teniendo prioridad sobre el guard', () => {
  const v = validate(normalizado({ recordType: 'venta', producto: 'novillo', monto: 100 }), 'gestor_campo', 0.95);
  assert.equal(v.denied, true, 'un rol sin permiso no debería recibir un consejo de reformulación');
});
