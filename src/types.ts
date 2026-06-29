// Tipos del dominio Parva. Sin `enum` (no lo soporta el type-stripping de Node):
// usamos uniones de strings + objetos `as const`.

export type Rol = 'owner' | 'gestor_campo';
export type TipoCampo = 'agricola' | 'ganadero' | 'mixto';

// Movimientos de la "gestión diaria" / económicos.
export type MovTipo = 'insumo' | 'labor' | 'gasto' | 'venta';

// Eventos de hacienda (ganadero).
export type EventoHaciendaTipo = 'nacimiento' | 'muerte' | 'compra' | 'venta' | 'traslado';

// Lo que el parser puede entender de un mensaje.
export type Intent = 'create_record' | 'query' | 'confirm' | 'unknown';
export type RecordType = MovTipo | 'evento_hacienda' | 'evento_sanitario';
export type QueryMetric = 'stock_animal' | 'margen' | 'gasto_total' | 'venta_total';

export interface ParsedFields {
  producto?: string;
  cantidad?: number;
  unidad?: string;
  monto?: number;
  moneda?: string;
  loteRef?: string;
  categoriaAnimal?: string;
  eventoTipo?: EventoHaciendaTipo;
  laborTipo?: string;
  categoria?: string;
  fecha?: string;
  descripcion?: string;
}

export interface ParsedQuery {
  metric: QueryMetric;
  loteRef?: string;
  categoriaAnimal?: string;
}

// Salida del parser (mismo shape que daría GPT-4o mini con structured outputs).
export interface ParsedIntent {
  intent: Intent;
  recordType: RecordType | null;
  fields: ParsedFields;
  query: ParsedQuery | null;
  confidence: number;
  rawText: string;
}

// Contexto del remitente, ya resuelto por teléfono (aislamiento por tenant).
export interface Sender {
  productorId: number;
  tipoCampo: TipoCampo;
  usuarioId: number;
  usuarioNombre: string;
  rol: Rol;
}

// Resultado de procesar un mensaje: lo que el bot responde + metadata para la UI.
export interface ProcessResult {
  reply: string;
  intent: Intent;
  status: 'created' | 'needs_confirmation' | 'confirmed' | 'denied' | 'query_answer' | 'unknown';
  confidence: number;
  detail?: unknown;
}
