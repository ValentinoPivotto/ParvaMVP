// Semilla de datos demo. Crea dos productores que ejercitan las dos variantes:
//  - "Estancia La Esperanza" (agrícola): lotes, movimientos y márgenes por lote.
//  - "Don Pedro e Hijos" (ganadero): hacienda, eventos y sanidad.
import { db, dropAll, isEmpty, initSchema, lastId } from './db.ts';
import { fileURLToPath } from 'node:url';

function hoy(offsetDias = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDias);
  return d.toISOString().slice(0, 10);
}

function insProductor(nombre: string, pais: string, tipo: string): number {
  return lastId(db.prepare('INSERT INTO productor (nombre, pais, tipo_campo) VALUES (?,?,?)').run(nombre, pais, tipo));
}
function insUsuario(pid: number, nombre: string, tel: string, rol: string): number {
  return lastId(db.prepare('INSERT INTO usuario (productor_id, nombre, telefono, rol) VALUES (?,?,?,?)').run(pid, nombre, tel, rol));
}
function insCampo(pid: number, nombre: string, ha: number): number {
  return lastId(db.prepare('INSERT INTO campo (productor_id, nombre, hectareas) VALUES (?,?,?)').run(pid, nombre, ha));
}
function insLote(pid: number, cid: number, nombre: string, numero: string, ha: number, uso: string): number {
  return lastId(db.prepare('INSERT INTO lote (productor_id, campo_id, nombre, numero, hectareas, uso_actual) VALUES (?,?,?,?,?,?)').run(pid, cid, nombre, numero, ha, uso));
}
function insCampania(pid: number, nombre: string, act: string): number {
  return lastId(db.prepare('INSERT INTO campania (productor_id, nombre, cultivo_actividad, fecha_inicio) VALUES (?,?,?,?)').run(pid, nombre, act, hoy(-120)));
}
function mov(pid: number, tipo: string, loteId: number | null, fecha: string, producto: string | null, cantidad: number | null, unidad: string | null, monto: number, categoria: string | null, by: number): void {
  db.prepare(`INSERT INTO movimiento (productor_id, tipo, lote_id, fecha, producto, cantidad, unidad, monto, moneda, categoria, origen, created_by)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`)
    .run(pid, tipo, loteId, fecha, producto, cantidad, unidad, monto, 'ARS', categoria, 'seed', by);
}

export function seed(): void {
  initSchema();

  // ===== Productor 1: agrícola =====
  const p1 = insProductor('Estancia La Esperanza', 'Argentina', 'agricola');
  const u1o = insUsuario(p1, 'Juan Pérez', '+5491100000001', 'owner');
  const u1g = insUsuario(p1, 'Juan Gómez', '+5491100000002', 'gestor_campo');
  const c1 = insCampo(p1, 'Campo Norte', 320);
  const l1 = insLote(p1, c1, 'Lote 1', '1', 80, 'Soja');
  const l4 = insLote(p1, c1, 'Lote 4', '4', 100, 'Maíz');
  const l7 = insLote(p1, c1, 'Lote 7', '7', 60, 'Trigo');
  insCampania(p1, 'Gruesa 2025/26', 'Soja / Maíz');

  // Lote 1 (soja): costos + venta => margen confiable y positivo.
  mov(p1, 'insumo', l1, hoy(-110), 'Semilla soja', 80, 'bolsa', 1_200_000, 'semilla', u1o);
  mov(p1, 'insumo', l1, hoy(-108), 'Fertilizante', 4000, 'kg', 2_500_000, 'fertilizante', u1g);
  mov(p1, 'labor', l1, hoy(-107), 'Siembra', null, null, 800_000, 'labor', u1g);
  mov(p1, 'gasto', l1, hoy(-60), 'Fumigación', null, null, 600_000, 'servicios', u1g);
  mov(p1, 'venta', l1, hoy(-10), 'Soja', 240, 'tn', 9_600_000, 'grano', u1o);

  // Lote 4 (maíz): solo costos, sin venta => margen NO confiable (lo demuestra la UI).
  mov(p1, 'insumo', l4, hoy(-100), 'Semilla maíz', 1, 'bolsa', 1_500_000, 'semilla', u1o);
  mov(p1, 'insumo', l4, hoy(-98), 'Urea', 5000, 'kg', 1_800_000, 'fertilizante', u1g);
  mov(p1, 'labor', l4, hoy(-97), 'Siembra', null, null, 900_000, 'labor', u1g);

  // ===== Productor 2: ganadero =====
  const p2 = insProductor('Don Pedro e Hijos', 'Argentina', 'ganadero');
  const u2o = insUsuario(p2, 'Pedro Díaz', '+5491100000003', 'owner');
  const u2g = insUsuario(p2, 'Marta Ruiz', '+5491100000004', 'gestor_campo');
  const c2 = insCampo(p2, 'La Lomada', 500);

  // Stock inicial de hacienda.
  for (const [cat, cant] of [['vaca', 120], ['ternero', 45], ['novillo', 60], ['vaquillona', 30], ['toro', 4]] as const) {
    db.prepare('INSERT INTO hacienda (productor_id, campo_id, categoria, cantidad) VALUES (?,?,?,?)').run(p2, c2, cat, cant);
  }
  // Eventos históricos (insertados directo, ya reflejados en el stock inicial).
  db.prepare('INSERT INTO evento_hacienda (productor_id, campo_id, tipo, categoria, cantidad, fecha, origen, created_by) VALUES (?,?,?,?,?,?,?,?)')
    .run(p2, c2, 'nacimiento', 'ternero', 12, hoy(-25), 'seed', u2g);
  db.prepare('INSERT INTO evento_hacienda (productor_id, campo_id, tipo, categoria, cantidad, monto, fecha, origen, created_by) VALUES (?,?,?,?,?,?,?,?,?)')
    .run(p2, c2, 'venta', 'novillo', 15, 7_500_000, hoy(-15), 'seed', u2o);

  // Movimientos económicos del ganadero.
  mov(p2, 'gasto', null, hoy(-40), 'Ración', 8000, 'kg', 1_200_000, 'alimentacion', u2o);
  mov(p2, 'venta', null, hoy(-15), 'Venta novillos', 15, 'cabeza', 7_500_000, 'hacienda', u2o);

  // Sanidad.
  db.prepare('INSERT INTO evento_sanitario (productor_id, campo_id, producto, categoria, cantidad, fecha, origen, created_by) VALUES (?,?,?,?,?,?,?,?)')
    .run(p2, c2, 'Vacuna aftosa', 'todos', 259, hoy(-30), 'seed', u2g);

  console.log('✓ Semilla cargada: 2 productores (agrícola + ganadero).');
}

export function seedIfEmpty(): void {
  if (isEmpty()) seed();
}

// Ejecución directa: `node src/seed.ts` (o `--reset` para borrar y recargar).
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv.includes('--reset')) {
    dropAll();
    console.log('✓ Base reiniciada.');
  }
  seed();
}
