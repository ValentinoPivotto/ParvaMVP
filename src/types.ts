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

/** Palabra del productor → categoría canónica de hacienda. Única fuente de
 *  verdad: la usan el parser (para extraer) y el validator (para detectar un
 *  registro de animales mal tipado). */
export const CATEGORIAS_ANIMAL: Record<string, string> = {
  ternero: 'ternero', terneros: 'ternero', ternera: 'ternero', terneras: 'ternero',
  vaca: 'vaca', vacas: 'vaca',
  novillo: 'novillo', novillos: 'novillo',
  vaquillona: 'vaquillona', vaquillonas: 'vaquillona',
  toro: 'toro', toros: 'toro',
};

/** Categoría canónica si el texto nombra animales, si no `undefined`.
 *
 *  Acepta `unknown` a propósito: los `fields` llegan de `JSON.parse` de la
 *  respuesta del modelo, donde el tipo es una promesa y no una garantía. Un
 *  número en `producto` haría estallar `processMessage()` antes de contestarle
 *  al productor. */
export function detectarCategoriaAnimal(texto: unknown): string | undefined {
  if (typeof texto !== 'string') return undefined;
  const t = texto.toLowerCase();
  for (const palabra of Object.keys(CATEGORIAS_ANIMAL)) {
    if (new RegExp(`\\b${palabra}\\b`).test(t)) return CATEGORIAS_ANIMAL[palabra];
  }
  return undefined;
}

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

// Salida del parser. Mismo shape para los tres caminos (Bedrock, local, mock).
export interface ParsedIntent {
  /** Motor que produjo este resultado. Un modelo que falla cae al mock en
   *  silencio, así que sin esto no hay forma de saber qué lo parseó. */
  motor?: 'mock' | 'bedrock' | 'local';
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
