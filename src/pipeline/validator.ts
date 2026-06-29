// Validator: reglas de negocio + permisos por rol + umbral de confianza.
// Decide si un registro se persiste, se rechaza por permiso, o pide confirmación.
import { config } from '../config.ts';
import { puedeCrear } from '../permissions.ts';
import type { Rol } from '../types.ts';
import type { Normalized } from './normalizer.ts';

export interface Validation {
  ok: boolean;                 // listo para persistir
  needsConfirmation: boolean;  // ambiguo / baja confianza => preguntar
  denied: boolean;             // sin permiso por rol
  motivo: string;              // mensaje para el usuario
}

function camposRequeridos(n: Normalized): string | null {
  switch (n.recordType) {
    case 'insumo':
      if (!n.producto) return 'no entendí qué insumo';
      if (n.cantidad == null && n.monto == null) return 'no entendí cantidad ni monto';
      return null;
    case 'gasto':
      if (n.monto == null) return 'no entendí el monto del gasto';
      return null;
    case 'venta':
      if (n.monto == null) return 'no entendí el monto de la venta';
      return null;
    case 'labor':
      return null; // la labor puede no tener monto
    case 'evento_hacienda':
      if (!n.categoria) return 'no entendí la categoría de hacienda';
      if (n.cantidad == null) return 'no entendí la cantidad de animales';
      if (!n.eventoTipo) return 'no entendí qué pasó con los animales';
      return null;
    case 'evento_sanitario':
      if (!n.producto) return 'no entendí el producto sanitario';
      return null;
    default:
      return 'tipo de registro desconocido';
  }
}

export function validate(n: Normalized, rol: Rol, confidence: number): Validation {
  // 1) Permiso por rol (guardrail).
  if (!puedeCrear(rol, n.recordType)) {
    return { ok: false, needsConfirmation: false, denied: true, motivo: `Tu rol (${rol}) no puede registrar ${n.recordType}.` };
  }
  // 2) Lote mencionado pero no resuelto => ambiguo.
  if (n.loteRef && !n.loteResuelto) {
    return { ok: false, needsConfirmation: true, denied: false, motivo: `no encontré el lote "${n.loteRef}"` };
  }
  // 3) Campos requeridos.
  const falta = camposRequeridos(n);
  if (falta) {
    return { ok: false, needsConfirmation: true, denied: false, motivo: falta };
  }
  // 4) Confianza por debajo del umbral => confirmar antes de guardar.
  if (confidence < config.confidenceThreshold) {
    return { ok: false, needsConfirmation: true, denied: false, motivo: 'no estoy seguro de haber entendido bien' };
  }
  return { ok: true, needsConfirmation: false, denied: false, motivo: 'ok' };
}
