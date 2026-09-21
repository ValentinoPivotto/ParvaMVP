// Cálculo de márgenes por lote. Principio del spec (§4.4): un margen se muestra
// solo si es confiable; si faltan ventas o costos, se indica en vez de mentir.
import { listLotes, totalesPorLote } from '../repository/repo.ts';

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

export function margenPorLote(productorId: number): MargenLote[] {
  return listLotes(productorId).map((l) => {
    const { ventas, costos } = totalesPorLote(productorId, l.id);
    let confiable = true;
    let razon: string | undefined;
    if (ventas === 0) { confiable = false; razon = 'sin ventas registradas'; }
    else if (costos === 0) { confiable = false; razon = 'sin costos registrados'; }
    return { loteId: l.id, loteNombre: l.nombre, uso: l.uso_actual, ventas, costos, margen: ventas - costos, confiable, razon };
  });
}
