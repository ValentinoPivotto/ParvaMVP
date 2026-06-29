// Parser: convierte el mensaje informal en un ParsedIntent estructurado.
// Por default usa un MOCK determinístico (reglas en español). Con OPENAI_API_KEY
// usaría GPT-4o mini con structured outputs (mismo shape de salida).
import { useRealAI, config } from '../config.ts';
import type { ParsedIntent, ParsedFields, RecordType, EventoHaciendaTipo } from '../types.ts';

const CATEGORIAS_ANIMAL: Record<string, string> = {
  ternero: 'ternero', terneros: 'ternero', ternera: 'ternero', terneras: 'ternero',
  vaca: 'vaca', vacas: 'vaca',
  novillo: 'novillo', novillos: 'novillo',
  vaquillona: 'vaquillona', vaquillonas: 'vaquillona',
  toro: 'toro', toros: 'toro',
};

const UNIDADES = 'litros?|lts?|l|kg|kilos?|tn|toneladas?|ton|bolsas?|cabezas?|unidades?|has?|hect[aá]reas?';

function detectarCategoriaAnimal(t: string): string | undefined {
  for (const palabra of Object.keys(CATEGORIAS_ANIMAL)) {
    if (new RegExp(`\\b${palabra}\\b`).test(t)) return CATEGORIAS_ANIMAL[palabra];
  }
  return undefined;
}

function num(s: string): number {
  // Tolera "1.200.000" (miles con punto) y "2,5" (decimal con coma).
  let x = s.trim();
  if (x.includes(',')) x = x.replace(/\./g, '').replace(',', '.');
  else if ((x.match(/\./g) ?? []).length > 1) x = x.replace(/\./g, '');
  else if (/\.\d{3}$/.test(x)) x = x.replace(/\./g, '');
  return parseFloat(x);
}

function extraerMonto(t: string): number | undefined {
  const m = t.match(/\$\s*([\d.,]+)/) ?? t.match(/([\d.,]+)\s*pesos/);
  return m ? num(m[1]) : undefined;
}

function extraerCantidadUnidad(t: string): { cantidad?: number; unidad?: string } {
  const sinMonto = t.replace(/\$\s*[\d.,]+/g, ' ').replace(/[\d.,]+\s*pesos/g, ' ');
  const m = sinMonto.match(new RegExp(`(\\d+(?:[.,]\\d+)?)\\s*(${UNIDADES})?`, 'i'));
  if (!m) return {};
  return { cantidad: num(m[1]), unidad: m[2] ? m[2].toLowerCase() : undefined };
}

function extraerLoteRef(t: string): string | undefined {
  const m = t.match(/lote\s*([a-zA-Z0-9]+)/);
  return m ? m[1] : undefined;
}

function extraerFecha(t: string): string {
  const d = new Date();
  if (/\bayer\b/.test(t)) d.setDate(d.getDate() - 1);
  else if (/anteayer/.test(t)) d.setDate(d.getDate() - 2);
  return d.toISOString().slice(0, 10);
}

function extraerProducto(t: string): string | undefined {
  const conocidos = ['gasoil', 'gas oil', 'fertilizante', 'urea', 'semilla', 'agroquímico', 'agroquimico', 'glifosato', 'herbicida', 'ración', 'racion', 'soja', 'maíz', 'maiz', 'trigo'];
  for (const p of conocidos) if (t.includes(p)) return p;
  const m = t.match(/de\s+([a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?)/i);
  return m ? m[1].trim() : undefined;
}

// --- Mock principal -------------------------------------------------------

export function parseMock(texto: string): ParsedIntent {
  const t = texto.toLowerCase().trim();
  const base: ParsedIntent = { intent: 'unknown', recordType: null, fields: {}, query: null, confidence: 0, rawText: texto };

  // 1) Confirmación (sin \b: no asierta bien tras vocal acentuada en regex sin flag u)
  if (/^(s[ií]|sip|dale|ok(ey)?|oka|listo|correcto|confirmo|confirm[aá]|exacto|as[ií] es|de una|tal cual|s[ií] dale)(\s|$|[,.!])/.test(t)) {
    return { ...base, intent: 'confirm', confidence: 0.99 };
  }

  const categoriaAnimal = detectarCategoriaAnimal(t);
  const loteRef = extraerLoteRef(t);
  const { cantidad, unidad } = extraerCantidadUnidad(t);
  const monto = extraerMonto(t);
  const fecha = extraerFecha(t);
  const esPregunta = /\?|cu[aá]nt|cu[aá]l|qu[eé]\b|tengo|hay\b|stock|mostr|dec[ií]me/.test(t);

  // 2) Consultas
  if (esPregunta) {
    if (/margen/.test(t)) return { ...base, intent: 'query', confidence: 0.92, query: { metric: 'margen', loteRef } };
    if (categoriaAnimal || /stock|hacienda|animales|cabezas/.test(t)) return { ...base, intent: 'query', confidence: 0.9, query: { metric: 'stock_animal', categoriaAnimal } };
    if (/gast/.test(t)) return { ...base, intent: 'query', confidence: 0.88, query: { metric: 'gasto_total' } };
    if (/vend|venta/.test(t)) return { ...base, intent: 'query', confidence: 0.88, query: { metric: 'venta_total' } };
  }

  // 3) Eventos de hacienda (hay categoría animal + verbo)
  if (categoriaAnimal) {
    let tipo: EventoHaciendaTipo | undefined;
    if (/naci/.test(t)) tipo = 'nacimiento';
    else if (/muri|murieron|se murió|se murio|perd[ií]/.test(t)) tipo = 'muerte';
    else if (/compr/.test(t)) tipo = 'compra';
    else if (/vend/.test(t)) tipo = 'venta';
    else if (/traslad|pas[eé]|mov[ií]/.test(t)) tipo = 'traslado';
    if (tipo) {
      const fields: ParsedFields = { categoriaAnimal, eventoTipo: tipo, cantidad, monto, fecha };
      const conf = cantidad != null ? 0.9 : 0.55;
      return { ...base, intent: 'create_record', recordType: 'evento_hacienda', fields, confidence: conf };
    }
  }

  // 4) Sanidad
  if (/vacun|desparasit|tratamiento|sanidad|dosis/.test(t)) {
    const fields: ParsedFields = { producto: extraerProducto(t) ?? 'sanidad', categoria: categoriaAnimal ?? 'todos', cantidad, fecha };
    return { ...base, intent: 'create_record', recordType: 'evento_sanitario', fields, confidence: 0.82 };
  }

  // 5) Labores
  if (/sembr|pulveric|fumig|cosech|apliqu|fertilic|ar[ée]\b|rastr|disc/.test(t)) {
    const fields: ParsedFields = { laborTipo: extraerProducto(t) ?? 'labor', loteRef, monto, fecha, descripcion: texto };
    return { ...base, intent: 'create_record', recordType: 'labor', fields, confidence: loteRef ? 0.85 : 0.7 };
  }

  // 6) Venta de grano / producto (sin categoría animal)
  if (/vend|venta/.test(t)) {
    const fields: ParsedFields = { producto: extraerProducto(t), cantidad, unidad, monto, loteRef, fecha };
    const conf = monto != null ? 0.88 : 0.6;
    return { ...base, intent: 'create_record', recordType: 'venta', fields, confidence: conf };
  }

  // 7) Gasto
  if (/pagu[ée]|gast[ée]|abon[ée]|gasto/.test(t)) {
    const fields: ParsedFields = { producto: extraerProducto(t), monto, loteRef, fecha, descripcion: texto };
    const conf = monto != null ? 0.85 : 0.5;
    return { ...base, intent: 'create_record', recordType: 'gasto', fields, confidence: conf };
  }

  // 8) Compra de insumo
  if (/compr[ée]|compre|carg[ué]/.test(t)) {
    const fields: ParsedFields = { producto: extraerProducto(t), cantidad, unidad, monto, loteRef, fecha };
    const completo = fields.producto != null && cantidad != null;
    return { ...base, intent: 'create_record', recordType: 'insumo', fields, confidence: completo ? 0.88 : 0.62 };
  }

  return base;
}

// --- Caminos con modelo (mismo shape de salida que el mock) --------------

// Prompt e instrucciones compartidos por OpenAI y por el modelo local.
const INSTRUCCIONES = `Sos el parser de un ERP agropecuario argentino. Convertí el mensaje informal del productor en un JSON EXACTO:
{
 "intent": "create_record" | "query" | "confirm" | "unknown",
 "recordType": "insumo"|"labor"|"gasto"|"venta"|"evento_hacienda"|"evento_sanitario"|null,
 "fields": { "producto"?, "cantidad"?(number), "unidad"?, "monto"?(number), "loteRef"?, "categoriaAnimal"?("ternero"|"vaca"|"novillo"|"vaquillona"|"toro"), "eventoTipo"?("nacimiento"|"muerte"|"compra"|"venta"|"traslado"), "laborTipo"?, "fecha"?("YYYY-MM-DD"), "descripcion"? },
 "query": { "metric": "stock_animal"|"margen"|"gasto_total"|"venta_total", "loteRef"?, "categoriaAnimal"? } | null,
 "confidence": number 0..1
}
Reglas: montos como número sin separador de miles. "lote 4" => loteRef "4". Si hay animales (terneros, vacas, novillos) => recordType "evento_hacienda" con eventoTipo. Preguntas => intent "query". Respondé SOLO el JSON, sin texto extra.`;

const EJEMPLOS: { u: string; a: Record<string, unknown> }[] = [
  { u: 'Compré 200 litros de gasoil para el lote 4', a: { intent: 'create_record', recordType: 'insumo', fields: { producto: 'gasoil', cantidad: 200, unidad: 'L', loteRef: '4' }, query: null, confidence: 0.95 } },
  { u: 'Nacieron 8 terneros', a: { intent: 'create_record', recordType: 'evento_hacienda', fields: { categoriaAnimal: 'ternero', eventoTipo: 'nacimiento', cantidad: 8 }, query: null, confidence: 0.95 } },
  { u: '¿Cuál es el margen del lote 1?', a: { intent: 'query', recordType: null, fields: {}, query: { metric: 'margen', loteRef: '1' }, confidence: 0.95 } },
];

function construirMensajes(texto: string): { role: string; content: string }[] {
  const msgs: { role: string; content: string }[] = [{ role: 'system', content: INSTRUCCIONES }];
  for (const e of EJEMPLOS) {
    msgs.push({ role: 'user', content: e.u });
    msgs.push({ role: 'assistant', content: JSON.stringify(e.a) });
  }
  msgs.push({ role: 'user', content: texto });
  return msgs;
}

function normalizarSalida(p: any, texto: string): ParsedIntent {
  return {
    intent: p?.intent ?? 'unknown',
    recordType: p?.recordType ?? null,
    fields: p?.fields ?? {},
    query: p?.query ?? null,
    confidence: typeof p?.confidence === 'number' ? p.confidence : 0.8,
    rawText: texto,
  };
}

// Camino A: OpenAI (GPT-4o mini) — corre en los servidores de OpenAI.
async function parseOpenAI(texto: string): Promise<ParsedIntent> {
  const res = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${config.openaiApiKey}` },
    body: JSON.stringify({ model: 'gpt-4o-mini', temperature: 0, response_format: { type: 'json_object' }, messages: construirMensajes(texto) }),
  });
  if (!res.ok) throw new Error('openai ' + res.status);
  const json = (await res.json()) as any;
  return normalizarSalida(JSON.parse(json.choices[0].message.content), texto);
}

// Camino B: modelo chico LOCAL vía Ollama (ej. qwen2.5:3b, llama3.2:3b).
// Corre en tu propia máquina; no requiere key ni costo por token.
async function parseLocal(texto: string): Promise<ParsedIntent> {
  const res = await fetch(`${config.ollamaUrl}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: config.localModel, stream: false, format: 'json', options: { temperature: 0 }, messages: construirMensajes(texto) }),
  });
  if (!res.ok) throw new Error('ollama ' + res.status);
  const json = (await res.json()) as any;
  return normalizarSalida(JSON.parse(json.message.content), texto);
}

// Probe único y cacheado: ¿está Ollama disponible? (no chequea en cada mensaje)
let ollamaProbe: Promise<boolean> | null = null;
function ollamaDisponible(): Promise<boolean> {
  if (!ollamaProbe) {
    ollamaProbe = fetch(`${config.ollamaUrl}/api/tags`, { signal: AbortSignal.timeout(800) })
      .then((r) => r.ok)
      .catch(() => false);
  }
  return ollamaProbe;
}

// Selector de modo. En 'auto': OpenAI si hay key → si no, modelo local si Ollama
// está disponible → si no, el mock. Cualquier falla del modelo cae al mock.
export async function parse(texto: string): Promise<ParsedIntent> {
  const mode = config.parserMode;
  if (mode === 'openai' || (mode === 'auto' && useRealAI())) {
    try { return await parseOpenAI(texto); } catch { /* fallback */ }
  }
  if (mode === 'local' || (mode === 'auto' && !useRealAI() && (await ollamaDisponible()))) {
    try { return await parseLocal(texto); } catch { /* fallback */ }
  }
  return parseMock(texto);
}

// Describe qué motor quedará activo (para el log de arranque).
export async function parserActivo(): Promise<string> {
  const mode = config.parserMode;
  if (mode === 'mock') return 'mock (reglas determinísticas)';
  if (mode === 'openai') return useRealAI() ? 'OpenAI gpt-4o-mini' : 'mock (falta OPENAI_API_KEY)';
  if (mode === 'local') return (await ollamaDisponible()) ? `local · ${config.localModel} (Ollama)` : 'mock (Ollama no responde)';
  if (useRealAI()) return 'OpenAI gpt-4o-mini (auto)';
  if (await ollamaDisponible()) return `local · ${config.localModel} (Ollama, auto)`;
  return 'mock (auto: sin key ni Ollama)';
}
