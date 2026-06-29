// Cálculo de márgenes por lote. Principio del spec (§4.4): un margen se muestra
// solo si es confiable; si faltan ventas o costos, se indica en vez de mentir.
import { db } from '../db.ts';
import { listLotes } from '../repo.ts';

export interface MargenLote {
  loteId: number;
  loteNombre: string;
  uso: string | null;
  ventas: number;
  costos: number;
  margen: number;
  confiable: boolean;
  razon?: string;
}

function suma(productorId: number, loteId: number, where: string): number {
  const row = db.prepare(
    `SELECT COALESCE(SUM(monto),0) AS s FROM movimiento WHERE productor_id=? AND lote_id=? AND ${where}`
  ).get(productorId, loteId) as { s: number };
  return row.s;
}

export function margenPorLote(productorId: number): MargenLote[] {
  return listLotes(productorId).map((l) => {
    const ventas = suma(productorId, l.id, "tipo='venta'");
    const costos = suma(productorId, l.id, "tipo IN ('insumo','labor','gasto')");
    let confiable = true;
    let razon: string | undefined;
    if (ventas === 0) { confiable = false; razon = 'sin ventas registradas'; }
    else if (costos === 0) { confiable = false; razon = 'sin costos registrados'; }
    return { loteId: l.id, loteNombre: l.nombre, uso: l.uso_actual, ventas, costos, margen: ventas - costos, confiable, razon };
  });
}
