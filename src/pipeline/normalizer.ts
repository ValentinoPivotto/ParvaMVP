// Normalizer: resuelve referencias informales a datos canónicos del tenant
// (lote por número/nombre, unidades canónicas, categoría, fecha).
import { findLoteByRef } from '../repo.ts';
import type { ParsedIntent, RecordType, EventoHaciendaTipo } from '../types.ts';

const UNIDAD_CANON: Record<string, string> = {
  l: 'L', lt: 'L', lts: 'L', litro: 'L', litros: 'L',
  kg: 'kg', kilo: 'kg', kilos: 'kg',
  tn: 'tn', ton: 'tn', tonelada: 'tn', toneladas: 'tn',
  bolsa: 'bolsa', bolsas: 'bolsa',
  cabeza: 'cabeza', cabezas: 'cabeza',
  unidad: 'u', unidades: 'u',
  ha: 'ha', has: 'ha', hectarea: 'ha', hectareas: 'ha', 'hectárea': 'ha', 'hectáreas': 'ha',
};

export interface Normalized {
  recordType: RecordType;
  loteId: number | null;
  loteRef?: string;
  loteResuelto: boolean;     // false si se mencionó un lote pero no se pudo resolver
  producto?: string;
  cantidad?: number;
  unidad?: string;
  monto?: number;
  categoria?: string;
  eventoTipo?: EventoHaciendaTipo;
  laborTipo?: string;
  fecha: string;
  descripcion?: string;
}

export function normalize(parsed: ParsedIntent, productorId: number): Normalized {
  const f = parsed.fields;
  let loteId: number | null = null;
  let resuelto = true;
  if (f.loteRef) {
    const l = findLoteByRef(productorId, f.loteRef);
    if (l) loteId = l.id;
    else resuelto = false;
  }
  return {
    recordType: parsed.recordType as RecordType,
    loteId,
    loteRef: f.loteRef,
    loteResuelto: resuelto,
    producto: f.producto,
    cantidad: f.cantidad,
    unidad: f.unidad ? (UNIDAD_CANON[f.unidad] ?? f.unidad) : undefined,
    monto: f.monto,
    categoria: f.categoriaAnimal ?? f.categoria,
    eventoTipo: f.eventoTipo,
    laborTipo: f.laborTipo,
    fecha: f.fecha ?? new Date().toISOString().slice(0, 10),
    descripcion: f.descripcion,
  };
}
