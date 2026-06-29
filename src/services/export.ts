// Export del "spreadsheet" a CSV (la planilla del productor, exportable).
import { listMovimientos, listHacienda } from '../repo.ts';
import { margenPorLote } from './margin.ts';

function fila(vals: unknown[]): string {
  return vals.map((v) => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }).join(',');
}

export function exportCsv(productorId: number, sheet: string): { filename: string; content: string } {
  if (sheet === 'hacienda') {
    const rows = listHacienda(productorId);
    const out = [fila(['categoria', 'cantidad'])];
    for (const r of rows) out.push(fila([r.categoria, r.cantidad]));
    return { filename: 'hacienda.csv', content: out.join('\n') };
  }

  if (sheet === 'margenes') {
    const rows = margenPorLote(productorId);
    const out = [fila(['lote', 'uso', 'ventas', 'costos', 'margen', 'confiable', 'razon'])];
    for (const r of rows) out.push(fila([r.loteNombre, r.uso, r.ventas, r.costos, r.margen, r.confiable ? 'sí' : 'no', r.razon ?? '']));
    return { filename: 'margenes.csv', content: out.join('\n') };
  }

  // default: movimientos
  const rows = listMovimientos(productorId);
  const out = [fila(['fecha', 'tipo', 'lote', 'producto', 'cantidad', 'unidad', 'monto', 'moneda', 'categoria', 'origen'])];
  for (const r of rows) out.push(fila([r.fecha, r.tipo, r.lote_nombre, r.producto, r.cantidad, r.unidad, r.monto, r.moneda, r.categoria, r.origen]));
  return { filename: 'movimientos.csv', content: out.join('\n') };
}
