// Matriz de permisos por rol (guardrail). Es la respuesta de §5.4 del spec.
import type { Rol, RecordType, QueryMetric } from './types.ts';

const PUEDE_CREAR: Record<Rol, RecordType[]> = {
  owner: ['insumo', 'labor', 'gasto', 'venta', 'evento_hacienda', 'evento_sanitario'],
  // El gestor de campo carga la operación, pero NO ventas.
  gestor_campo: ['insumo', 'labor', 'gasto', 'evento_hacienda', 'evento_sanitario'],
};

const PUEDE_CONSULTAR: Record<Rol, QueryMetric[]> = {
  owner: ['stock_animal', 'margen', 'gasto_total', 'venta_total'],
  // El gestor no ve márgenes ni ventas (info económica sensible).
  gestor_campo: ['stock_animal', 'gasto_total'],
};

export function puedeCrear(rol: Rol, rt: RecordType): boolean {
  return PUEDE_CREAR[rol].includes(rt);
}

export function puedeConsultar(rol: Rol, metric: QueryMetric): boolean {
  return PUEDE_CONSULTAR[rol].includes(metric);
}
