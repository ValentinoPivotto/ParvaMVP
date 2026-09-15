// Parser: convierte el mensaje informal en un ParsedIntent estructurado.
// Por default usa un MOCK determinístico (reglas en español). Con credenciales
// reales usa Nova Lite sobre Bedrock, GPT-4o mini o un modelo local vía Ollama,
// todos con el mismo shape de salida.
import { useRealAI, useBedrock, config } from '../config.ts';
import { firmarAws } from '../services/sigv4.ts';
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

// Prompt e instrucciones compartidos por Bedrock, OpenAI y el modelo local.
//
// La fecha de hoy va inyectada porque el modelo no tiene forma de saberla, y sin
// ella no puede resolver "ayer". El registro terminaba fechado hoy (normalize()
// completa la fecha faltante), que es peor que fallar: queda mal en la planilla
// y nadie se entera. El mock resuelve lo mismo en extraerFecha().
function instrucciones(hoy: string): string {
  return `Sos el parser de un ERP agropecuario argentino. Hoy es ${hoy}. Convertí el mensaje informal del productor en un JSON EXACTO:
{
 "intent": "create_record" | "query" | "confirm" | "unknown",
 "recordType": "insumo"|"labor"|"gasto"|"venta"|"evento_hacienda"|"evento_sanitario"|null,
 "fields": { "producto"?, "cantidad"?(number), "unidad"?, "monto"?(number), "loteRef"?, "categoriaAnimal"?("ternero"|"vaca"|"novillo"|"vaquillona"|"toro"), "eventoTipo"?("nacimiento"|"muerte"|"compra"|"venta"|"traslado"), "laborTipo"?, "fecha"?("YYYY-MM-DD"), "descripcion"? },
 "query": { "metric": "stock_animal"|"margen"|"gasto_total"|"venta_total", "loteRef"?, "categoriaAnimal"? } | null,
 "confidence": number 0..1
}
Reglas: preguntas => intent "query".
LOTE: si el mensaje nombra un lote, completá siempre "loteRef" con su número, sea cual sea el recordType. "lote 4" => loteRef "4".
NÚMEROS: la coma SIEMPRE es decimal, nunca separa miles. "3,5" es 3.5 y NO 3500. El punto sí separa miles: "1.800" es 1800; "4.250.000" es 4250000. Nunca conviertas un decimal en un entero.
CANTIDADES EN PALABRAS: contá también las escritas con letras. "un"/"una" es 1, "dos" es 2, "cuatro" es 4.
DIRECCIÓN DE LA PLATA: entre "venta" e "insumo" no decide el verbo sino hacia dónde va la plata. Si sale mercadería del campo y entra plata ("por $X", "a $X") => "venta", sea cual sea el verbo (vendí, entregué, despaché, mandé). Si el productor paga para recibir mercadería => "insumo".
PRODUCTO: en "venta" e "insumo" completá siempre "producto" con la mercadería, aunque la cantidad quede implícita.
ANIMALES: solo si el mensaje nombra animales (terneros, vacas, novillos, vaquillonas, toros) el recordType es "evento_hacienda" con su "eventoTipo", también cuando se compran o se venden. Nunca "venta" ni "gasto": si no es evento_hacienda, el stock no se descuenta.
SANIDAD (vacunas, dosis, antiparasitarios como ivermectina, tratamientos) => "evento_sanitario", nunca "labor".
FECHAS: devolvé siempre "fecha" en YYYY-MM-DD. "ayer" es el día anterior a ${hoy}; "anteayer", dos días antes. Si el mensaje no menciona ninguna fecha, usá ${hoy}.
Respondé SOLO el JSON, sin texto extra.`;
}

// Los ejemplos llevan fecha coherente con el "hoy" inyectado; si no, le estaría
// mostrando fechas que contradicen la regla que acaba de leer.
function ejemplos(hoy: string, ayer: string): { u: string; a: Record<string, unknown> }[] {
  return [
    { u: 'Compré 200 litros de gasoil para el lote 4', a: { intent: 'create_record', recordType: 'insumo', fields: { producto: 'gasoil', cantidad: 200, unidad: 'L', loteRef: '4', fecha: hoy }, query: null, confidence: 0.95 } },
    { u: 'compré 3,2 tn de balanceado', a: { intent: 'create_record', recordType: 'insumo', fields: { producto: 'balanceado', cantidad: 3.2, unidad: 'tn', fecha: hoy }, query: null, confidence: 0.95 } },
    { u: 'Nacieron 8 terneros', a: { intent: 'create_record', recordType: 'evento_hacienda', fields: { categoriaAnimal: 'ternero', eventoTipo: 'nacimiento', cantidad: 8, fecha: hoy }, query: null, confidence: 0.95 } },
    { u: 'ayer vendí 30 novillos a 1.200.000 en total', a: { intent: 'create_record', recordType: 'evento_hacienda', fields: { categoriaAnimal: 'novillo', eventoTipo: 'venta', cantidad: 30, monto: 1200000, fecha: ayer }, query: null, confidence: 0.95 } },
    { u: 'le di 3 dosis de ivermectina a las vacas del lote 2', a: { intent: 'create_record', recordType: 'evento_sanitario', fields: { producto: 'ivermectina', categoriaAnimal: 'vaca', cantidad: 3, loteRef: '2', fecha: hoy }, query: null, confidence: 0.9 } },
    { u: '¿Cuál es el margen del lote 1?', a: { intent: 'query', recordType: null, fields: {}, query: { metric: 'margen', loteRef: '1' }, confidence: 0.95 } },
  ];
}

function fechaISO(offsetDias: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDias);
  return d.toISOString().slice(0, 10);
}

function construirMensajes(texto: string): { role: string; content: string }[] {
  const hoy = fechaISO(0);
  const msgs: { role: string; content: string }[] = [{ role: 'system', content: instrucciones(hoy) }];
  for (const e of ejemplos(hoy, fechaISO(-1))) {
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

// Camino C: Amazon Nova Lite sobre Bedrock, vía la API Converse. La firma va a
// mano (services/sigv4.ts) para no traer el SDK de AWS.
//
// Converse no tiene un equivalente a `response_format`: el JSON se pide por
// prompt y recortarlo es responsabilidad nuestra.
async function parseBedrock(texto: string): Promise<ParsedIntent> {
  const msgs = construirMensajes(texto);
  const cuerpo = JSON.stringify({
    system: msgs.filter((m) => m.role === 'system').map((m) => ({ text: m.content })),
    messages: msgs
      .filter((m) => m.role !== 'system')
      .map((m) => ({ role: m.role, content: [{ text: m.content }] })),
    inferenceConfig: { temperature: 0, maxTokens: 512 },
  });

  const req = firmarAws({
    method: 'POST',
    host: `bedrock-runtime.${config.awsRegion}.amazonaws.com`,
    path: `/model/${encodeURIComponent(config.bedrockModelId)}/converse`,
    region: config.awsRegion,
    service: 'bedrock',
    body: cuerpo,
    creds: {
      accessKeyId: config.awsAccessKeyId,
      secretAccessKey: config.awsSecretAccessKey,
      sessionToken: config.awsSessionToken || undefined,
    },
  });

  const res = await fetch(req.url, { method: 'POST', headers: req.headers, body: req.body });
  if (!res.ok) throw new Error(`bedrock ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const json = (await res.json()) as any;
  return normalizarSalida(JSON.parse(recortarJson(json?.output?.message?.content?.[0]?.text ?? '')), texto);
}

// Sin JSON mode, el modelo a veces envuelve la respuesta en ```json … ``` o le
// cuelga una frase. Nos quedamos con el objeto más externo.
function recortarJson(s: string): string {
  const limpio = s.replace(/^\s*```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '');
  const i = limpio.indexOf('{');
  const j = limpio.lastIndexOf('}');
  return i >= 0 && j > i ? limpio.slice(i, j + 1) : limpio;
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

// Motor elegido en modo 'auto': Bedrock primero porque es el camino financiado
// por la universidad; OpenAI sale del bolsillo. El probe de Ollama solo corre si
// no hay ninguna credencial, para no sumarle 800 ms al primer mensaje.
async function motorAuto(): Promise<string | null> {
  if (useBedrock()) return 'bedrock';
  if (useRealAI()) return 'openai';
  if (await ollamaDisponible()) return 'local';
  return null;
}

// Selector de modo. Un modo explícito prueba solo su motor; 'auto' elige por
// credencial disponible. Cualquier falla del modelo cae al mock.
export async function parse(texto: string): Promise<ParsedIntent> {
  const mode = config.parserMode;
  const motor = mode === 'auto' ? await motorAuto() : mode === 'mock' ? null : mode;
  try {
    if (motor === 'bedrock') return await parseBedrock(texto);
    if (motor === 'openai') return await parseOpenAI(texto);
    if (motor === 'local') return await parseLocal(texto);
  } catch { /* fallback */ }
  return parseMock(texto);
}

// Describe qué motor quedará activo (para el log de arranque).
export async function parserActivo(): Promise<string> {
  const mode = config.parserMode;
  if (mode === 'mock') return 'mock (reglas determinísticas)';
  if (mode === 'bedrock') return useBedrock() ? `Bedrock · ${config.bedrockModelId} (${config.awsRegion})` : 'mock (faltan credenciales AWS)';
  if (mode === 'openai') return useRealAI() ? 'OpenAI gpt-4o-mini' : 'mock (falta OPENAI_API_KEY)';
  if (mode === 'local') return (await ollamaDisponible()) ? `local · ${config.localModel} (Ollama)` : 'mock (Ollama no responde)';
  if (useBedrock()) return `Bedrock · ${config.bedrockModelId} (${config.awsRegion}, auto)`;
  if (useRealAI()) return 'OpenAI gpt-4o-mini (auto)';
  if (await ollamaDisponible()) return `local · ${config.localModel} (Ollama, auto)`;
  return 'mock (auto: sin credenciales ni Ollama)';
}
