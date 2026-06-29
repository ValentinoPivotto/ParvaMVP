// Consultas de lectura del bot ("¿cuántos terneros tengo?", "¿cuál es el margen?").
// Respeta permisos por rol (el gestor no ve margen ni ventas).
import { db } from '../db.ts';
import { listHacienda, findLoteByRef } from '../repo.ts';
import { puedeConsultar } from '../permissions.ts';
import { margenPorLote } from './margin.ts';
import type { ParsedQuery, Rol } from '../types.ts';

export interface QueryAnswer { ok: boolean; text: string; data?: unknown; }

function fmt(n: number): string {
  return '$' + Math.round(n).toLocaleString('es-AR');
}

export function answerQuery(productorId: number, rol: Rol, q: ParsedQuery): QueryAnswer {
  if (!puedeConsultar(rol, q.metric)) {
    return { ok: false, text: `🚫 Tu rol (${rol}) no puede consultar ${q.metric}.` };
  }

  if (q.metric === 'stock_animal') {
    const filas = listHacienda(productorId).filter((h) => !q.categoriaAnimal || h.categoria === q.categoriaAnimal);
    if (filas.length === 0) {
      return { ok: true, text: q.categoriaAnimal ? `No tenés ${q.categoriaAnimal} registrados.` : 'No hay hacienda registrada.' };
    }
    const total = filas.reduce((a, h) => a + h.cantidad, 0);
    if (q.categoriaAnimal) return { ok: true, text: `Tenés ${total} ${q.categoriaAnimal}.`, data: filas };
    const detalle = filas.map((h) => `${h.cantidad} ${h.categoria}`).join(', ');
    return { ok: true, text: `Stock actual: ${detalle}. Total ${total} cabezas.`, data: filas };
  }

  if (q.metric === 'margen') {
    const todos = margenPorLote(productorId);
    if (q.loteRef) {
      const l = findLoteByRef(productorId, q.loteRef);
      const m = l ? todos.find((x) => x.loteId === l.id) : undefined;
      if (!m) return { ok: true, text: `No encontré el lote "${q.loteRef}".` };
      if (!m.confiable) return { ok: true, text: `El ${m.loteNombre} todavía no tiene margen confiable (${m.razon}). Costos cargados: ${fmt(m.costos)}.`, data: m };
      return { ok: true, text: `Margen del ${m.loteNombre}: ${fmt(m.margen)} (ventas ${fmt(m.ventas)} − costos ${fmt(m.costos)}).`, data: m };
    }
    const conf = todos.filter((m) => m.confiable);
    if (conf.length === 0) return { ok: true, text: 'Todavía no hay márgenes confiables (faltan ventas o costos cargados).', data: todos };
    const txt = conf.map((m) => `${m.loteNombre}: ${fmt(m.margen)}`).join(' · ');
    return { ok: true, text: `Márgenes por lote: ${txt}.`, data: todos };
  }

  if (q.metric === 'gasto_total') {
    const row = db.prepare("SELECT COALESCE(SUM(monto),0) AS s FROM movimiento WHERE productor_id=? AND tipo IN ('insumo','labor','gasto')").get(productorId) as { s: number };
    return { ok: true, text: `Gasto total registrado: ${fmt(row.s)}.` };
  }

  if (q.metric === 'venta_total') {
    const row = db.prepare("SELECT COALESCE(SUM(monto),0) AS s FROM movimiento WHERE productor_id=? AND tipo='venta'").get(productorId) as { s: number };
    return { ok: true, text: `Ventas totales registradas: ${fmt(row.s)}.` };
  }

  return { ok: false, text: 'No pude responder esa consulta.' };
}
