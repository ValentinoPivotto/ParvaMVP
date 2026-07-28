// Repositorio: única puerta de acceso a los datos. TODA lectura/escritura va
// filtrada por productor_id => aislamiento por tenant (guardrail principal).
import { db, lastId } from './db.ts';
import { phoneVariants } from './phone.ts';
import type { Sender } from './types.ts';

// --- Identidad / tenant ---------------------------------------------------

// Busca por variantes (no por match exacto) porque el wa_id argentino de Meta
// suele venir sin el 9 de móvil. Loop ordenado en vez de `IN (...)`: con `IN` el
// motor elegiría arbitrariamente si dos filas matchean variantes distintas.
export function getSenderByTelefono(telefono: string): Sender | null {
  const stmt = db.prepare(`
    SELECT u.id AS usuarioId, u.nombre AS usuarioNombre, u.rol AS rol,
           p.id AS productorId, p.tipo_campo AS tipoCampo
    FROM usuario u JOIN productor p ON p.id = u.productor_id
    WHERE u.telefono = ?
  `);
  for (const variante of phoneVariants(telefono)) {
    const row = stmt.get(variante) as any;
    if (!row) continue;
    return {
      productorId: row.productorId,
      tipoCampo: row.tipoCampo,
      usuarioId: row.usuarioId,
      usuarioNombre: row.usuarioNombre,
      rol: row.rol,
    };
  }
  return null;
}

export function listProductores(): any[] {
  return db.prepare('SELECT * FROM productor ORDER BY id').all();
}

export function getProductor(id: number): any {
  return db.prepare('SELECT * FROM productor WHERE id = ?').get(id);
}

export function listUsuarios(productorId: number): any[] {
  return db.prepare('SELECT * FROM usuario WHERE productor_id = ? ORDER BY id').all(productorId);
}

// --- Estructura productiva ------------------------------------------------

export function listCampos(productorId: number): any[] {
  return db.prepare('SELECT * FROM campo WHERE productor_id = ? ORDER BY id').all(productorId);
}

export function listLotes(productorId: number): any[] {
  return db.prepare('SELECT * FROM lote WHERE productor_id = ? ORDER BY id').all(productorId);
}

export function listCampanias(productorId: number): any[] {
  return db.prepare('SELECT * FROM campania WHERE productor_id = ? ORDER BY id').all(productorId);
}

// Resuelve una referencia informal de lote ("4", "lote 4", "norte") a un lote real.
export function findLoteByRef(productorId: number, ref: string): any | null {
  const r = ref.trim().toLowerCase();
  const lotes = listLotes(productorId);
  return (
    lotes.find((l) => String(l.numero ?? '').toLowerCase() === r) ??
    lotes.find((l) => String(l.nombre ?? '').toLowerCase() === r) ??
    lotes.find((l) => String(l.nombre ?? '').toLowerCase() === `lote ${r}`) ??
    lotes.find((l) => String(l.nombre ?? '').toLowerCase().includes(r)) ??
    null
  );
}

// --- Movimientos ----------------------------------------------------------

export interface NuevoMovimiento {
  productorId: number;
  tipo: string;
  loteId?: number | null;
  campaniaId?: number | null;
  fecha: string;
  producto?: string | null;
  cantidad?: number | null;
  unidad?: string | null;
  monto?: number | null;
  moneda?: string;
  categoria?: string | null;
  descripcion?: string | null;
  origen?: string;
  createdBy?: number | null;
}

export function insertMovimiento(m: NuevoMovimiento): number {
  const info = db.prepare(`
    INSERT INTO movimiento
      (productor_id, tipo, lote_id, campania_id, fecha, producto, cantidad, unidad,
       monto, moneda, categoria, descripcion, origen, created_by)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    m.productorId, m.tipo, m.loteId ?? null, m.campaniaId ?? null, m.fecha,
    m.producto ?? null, m.cantidad ?? null, m.unidad ?? null, m.monto ?? null,
    m.moneda ?? 'ARS', m.categoria ?? null, m.descripcion ?? null,
    m.origen ?? 'web', m.createdBy ?? null,
  );
  const id = lastId(info);
  audit(m.productorId, 'movimiento', id, 'create', m.createdBy ?? null, m.origen ?? 'web',
    `${m.tipo} ${m.producto ?? ''} ${m.monto ?? ''}`.trim());
  return id;
}

export function listMovimientos(productorId: number): any[] {
  return db.prepare(`
    SELECT m.*, l.nombre AS lote_nombre
    FROM movimiento m LEFT JOIN lote l ON l.id = m.lote_id
    WHERE m.productor_id = ? ORDER BY m.fecha DESC, m.id DESC
  `).all(productorId);
}

// --- Hacienda (ganadero) --------------------------------------------------

export function listHacienda(productorId: number): any[] {
  return db.prepare('SELECT * FROM hacienda WHERE productor_id = ? ORDER BY categoria').all(productorId);
}

// Ajusta el stock de una categoría (crea la fila si no existe). delta puede ser + o -.
export function adjustHacienda(productorId: number, campoId: number | null, categoria: string, delta: number): void {
  const fila = db.prepare(
    'SELECT * FROM hacienda WHERE productor_id = ? AND categoria = ?'
  ).get(productorId, categoria) as any;
  if (fila) {
    const nueva = Math.max(0, (fila.cantidad ?? 0) + delta);
    db.prepare('UPDATE hacienda SET cantidad = ? WHERE id = ?').run(nueva, fila.id);
  } else if (delta > 0) {
    db.prepare('INSERT INTO hacienda (productor_id, campo_id, categoria, cantidad) VALUES (?,?,?,?)')
      .run(productorId, campoId, categoria, delta);
  }
}

export interface NuevoEventoHacienda {
  productorId: number;
  campoId?: number | null;
  tipo: string;
  categoria: string;
  cantidad: number;
  monto?: number | null;
  fecha: string;
  origen?: string;
  createdBy?: number | null;
}

export function insertEventoHacienda(e: NuevoEventoHacienda): number {
  const info = db.prepare(`
    INSERT INTO evento_hacienda
      (productor_id, campo_id, tipo, categoria, cantidad, monto, fecha, origen, created_by)
    VALUES (?,?,?,?,?,?,?,?,?)
  `).run(
    e.productorId, e.campoId ?? null, e.tipo, e.categoria, e.cantidad,
    e.monto ?? null, e.fecha, e.origen ?? 'web', e.createdBy ?? null,
  );
  // El stock se mueve según el tipo de evento.
  const signo = (e.tipo === 'nacimiento' || e.tipo === 'compra') ? 1
    : (e.tipo === 'muerte' || e.tipo === 'venta') ? -1 : 0;
  if (signo !== 0) adjustHacienda(e.productorId, e.campoId ?? null, e.categoria, signo * e.cantidad);
  const id = lastId(info);
  audit(e.productorId, 'evento_hacienda', id, 'create', e.createdBy ?? null, e.origen ?? 'web',
    `${e.tipo} ${e.cantidad} ${e.categoria}`);
  return id;
}

export interface NuevoEventoSanitario {
  productorId: number;
  campoId?: number | null;
  producto?: string | null;
  categoria?: string | null;
  cantidad?: number | null;
  fecha: string;
  origen?: string;
  createdBy?: number | null;
}

export function insertEventoSanitario(e: NuevoEventoSanitario): number {
  const info = db.prepare(`
    INSERT INTO evento_sanitario
      (productor_id, campo_id, producto, categoria, cantidad, fecha, origen, created_by)
    VALUES (?,?,?,?,?,?,?,?)
  `).run(
    e.productorId, e.campoId ?? null, e.producto ?? null, e.categoria ?? null,
    e.cantidad ?? null, e.fecha, e.origen ?? 'web', e.createdBy ?? null,
  );
  const id = lastId(info);
  audit(e.productorId, 'evento_sanitario', id, 'create', e.createdBy ?? null, e.origen ?? 'web',
    `${e.producto ?? 'sanidad'} ${e.cantidad ?? ''}`.trim());
  return id;
}

export function listEventosSanitarios(productorId: number): any[] {
  return db.prepare('SELECT * FROM evento_sanitario WHERE productor_id = ? ORDER BY fecha DESC, id DESC').all(productorId);
}

// --- Mensajes crudos + confirmación ---------------------------------------

/**
 * Inserta el mensaje entrante. Devuelve el id nuevo, o `null` si `waMessageId`
 * ya existía: Meta reintenta los webhooks (entrega at-least-once) y sin esto
 * cada reintento duplicaría el registro.
 *
 * El índice único de `wa_message_id` es el que arbitra, no un SELECT previo, así
 * que no hay ventana de carrera entre dos reintentos concurrentes.
 */
export function insertRawMessage(
  productorId: number, usuarioId: number, texto: string, waMessageId?: string | null,
): number | null {
  const info = db.prepare(
    'INSERT OR IGNORE INTO raw_message (productor_id, usuario_id, texto, wa_message_id) VALUES (?,?,?,?)'
  ).run(productorId, usuarioId, texto, waMessageId ?? null);
  // ⚠ Con INSERT OR IGNORE hay que mirar `changes`: en un insert ignorado
  // lastInsertRowid conserva el rowid ANTERIOR, así que lastId() devolvería el
  // id de otra fila y el pipeline mutaría un raw_message ajeno en silencio.
  if (info.changes === 0) return null;
  return lastId(info);
}

export function updateRawMessage(
  id: number,
  intent: string,
  recordType: string | null,
  parsedJson: string,
  confidence: number,
  estado: string,
): void {
  db.prepare(`
    UPDATE raw_message SET intent = ?, record_type = ?, parsed_json = ?, confidence = ?, estado = ?
    WHERE id = ?
  `).run(intent, recordType, parsedJson, confidence, estado, id);
}

export function setRawEstado(id: number, estado: string): void {
  db.prepare('UPDATE raw_message SET estado = ? WHERE id = ?').run(estado, id);
}

export function getLastPending(productorId: number, usuarioId: number): any | null {
  return db.prepare(`
    SELECT * FROM raw_message
    WHERE productor_id = ? AND usuario_id = ? AND estado = 'pending' AND parsed_json IS NOT NULL
    ORDER BY id DESC LIMIT 1
  `).get(productorId, usuarioId) ?? null;
}

export function listRawMessages(productorId: number, limit = 20): any[] {
  return db.prepare(
    'SELECT * FROM raw_message WHERE productor_id = ? ORDER BY id DESC LIMIT ?'
  ).all(productorId, limit);
}

// --- Auditoría ------------------------------------------------------------

export function audit(
  productorId: number, entidad: string, entidadId: number | null,
  accion: string, usuarioId: number | null, origen: string | null, detalle: string,
): void {
  db.prepare(`
    INSERT INTO audit_log (productor_id, entidad, entidad_id, accion, usuario_id, origen, detalle)
    VALUES (?,?,?,?,?,?,?)
  `).run(productorId, entidad, entidadId, accion, usuarioId, origen, detalle);
}
