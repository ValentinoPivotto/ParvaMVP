// Eval del parser: corre los casos de casos.json contra el motor elegido y
// reporta cuántos salen bien.
//
// No es un test unitario. Los motores con modelo son no determinísticos y
// cuestan plata, así que esto no corre solo: se corre a mano cuando se toca el
// prompt o se cambia de modelo.
//
//   npm run eval                    # el motor que resuelva PARSER_MODE
//   npm run eval -- --motor=mock    # baseline sin credenciales ni costo
//   npm run eval -- --motor=bedrock # Nova Lite
//   npm run eval -- --caso=hacienda-singular
//   npm run eval -- --strict        # exit 1 si algo falla (para CI)
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

interface Caso {
  id: string;
  texto: string;
  nota: string;
  espera: {
    intent?: string;
    recordType?: string | null;
    fields?: Record<string, unknown>;
    contiene?: Record<string, string>;
    prohibe?: string[];
    query?: Record<string, unknown>;
  };
}

const args = process.argv.slice(2);
const opt = (n: string) => args.find((a) => a.startsWith(`--${n}=`))?.split('=')[1];
const motor = opt('motor');
const soloCaso = opt('caso');
const strict = args.includes('--strict');

const MOTORES = ['auto', 'mock', 'bedrock', 'local'];
if (motor && !MOTORES.includes(motor)) {
  // Sin esto, "--motor=bedrok" no matchea ninguna rama de parse(), corre el
  // mock, y el encabezado igual informa Bedrock: una medición falsa con
  // etiqueta que dice lo contrario.
  console.error(`Motor desconocido: "${motor}". Válidos: ${MOTORES.join(', ')}.`);
  process.exit(1);
}

// config.ts lee el entorno al importarse: esto tiene que pasar antes del import.
if (motor) process.env.PARSER_MODE = motor;
const { parse, parserActivo } = await import('../src/pipeline/parser.ts');

const casos: Caso[] = JSON.parse(
  readFileSync(fileURLToPath(new URL('./casos.json', import.meta.url)), 'utf8'),
).casos;

function fechaISO(offset: number, base = new Date()): string {
  const d = new Date(base);
  d.setDate(d.getDate() + offset);
  return d.toISOString().slice(0, 10);
}

// "hoy" | "-2" | "*" → la fecha concreta, o null si no es verificable.
//
// `base` es la instantánea tomada ANTES de la llamada al modelo: si se
// recalculara después, una corrida que cruza la medianoche compararía la
// respuesta contra el día siguiente y reportaría un fallo inexistente.
function fechaEsperada(v: unknown, base: Date): string | null {
  if (v === '*') return null;
  if (v === 'hoy') return fechaISO(0, base);
  if (typeof v === 'string' && /^-\d+$/.test(v)) return fechaISO(Number(v), base);
  return typeof v === 'string' ? v : null;
}

// Sin acentos y en minúscula: "maíz" y "maiz" son la misma palabra para el productor.
function plano(s: string): string {
  return s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
}

function revisar(c: Caso, r: any, base: Date): string[] {
  const fallas: string[] = [];
  const e = c.espera;

  if (e.intent !== undefined && r.intent !== e.intent) {
    fallas.push(`intent: ${r.intent} (esperaba ${e.intent})`);
  }
  if (e.recordType !== undefined && r.recordType !== e.recordType) {
    fallas.push(`recordType: ${r.recordType} (esperaba ${e.recordType})`);
  }

  for (const [k, v] of Object.entries(e.fields ?? {})) {
    const obtenido = r.fields?.[k];
    if (k === 'fecha') {
      const esperada = fechaEsperada(v, base);
      if (esperada === null) {
        if (obtenido == null) fallas.push('fecha: no devolvió ninguna');
      } else if (obtenido !== esperada) {
        fallas.push(`fecha: ${obtenido ?? '(ninguna)'} (esperaba ${esperada})`);
      }
      continue;
    }
    if (obtenido !== v) fallas.push(`${k}: ${JSON.stringify(obtenido)} (esperaba ${JSON.stringify(v)})`);
  }

  // Campos que NO deben venir. Sin esto solo se mide recall de los slots
  // elegidos: un caso "sin cantidad" pasa igual si el modelo la alucina.
  for (const k of e.prohibe ?? []) {
    const obtenido = r.fields?.[k];
    if (obtenido != null) fallas.push(`${k}: ${JSON.stringify(obtenido)} (no debería venir)`);
  }

  for (const [k, sub] of Object.entries(e.contiene ?? {})) {
    const obtenido = r.fields?.[k];
    if (typeof obtenido !== 'string' || !plano(obtenido).includes(plano(sub))) {
      fallas.push(`${k}: ${JSON.stringify(obtenido)} (esperaba que incluyera "${sub}")`);
    }
  }

  for (const [k, v] of Object.entries(e.query ?? {})) {
    const obtenido = r.query?.[k];
    if (obtenido !== v) fallas.push(`query.${k}: ${JSON.stringify(obtenido)} (esperaba ${JSON.stringify(v)})`);
  }

  return fallas;
}

// --- Corrida ---------------------------------------------------------------

const aCorrer = soloCaso ? casos.filter((c) => c.id === soloCaso) : casos;
if (aCorrer.length === 0) {
  console.error(`No hay ningún caso con id "${soloCaso}".`);
  process.exit(1);
}

// Qué motor se espera que parsee. parse() cae al mock ante cualquier error, así
// que la etiqueta de arranque no alcanza: hay que mirar caso por caso.
const esperado = motor && motor !== 'auto' ? motor : null;
console.log(`Motor: ${await parserActivo()}`);
console.log(`Casos: ${aCorrer.length}   (hoy = ${fechaISO(0)})\n` + '─'.repeat(78));

const fallados: { c: Caso; fallas: string[] }[] = [];
const porGrupo: Record<string, { ok: number; total: number }> = {};
const t0 = Date.now();

const motoresUsados: Record<string, number> = {};

for (const c of aCorrer) {
  const base = new Date();
  const r = await parse(c.texto);
  const usado = r.motor ?? 'desconocido';
  motoresUsados[usado] = (motoresUsados[usado] ?? 0) + 1;
  const fallas = revisar(c, r, base);
  const grupo = c.espera.recordType ?? c.espera.intent ?? 'otro';
  porGrupo[grupo] ??= { ok: 0, total: 0 };
  porGrupo[grupo].total++;

  if (fallas.length === 0) {
    porGrupo[grupo].ok++;
    console.log(`✅ ${c.id}${usado === esperado ? '' : `   ⚠️ ${usado}`}`);
  } else {
    fallados.push({ c, fallas });
    console.log(`❌ ${c.id}${usado === esperado ? '' : `   ⚠️ ${usado}`}  "${c.texto}"`);
    console.log(`     ${c.nota}`);
    for (const f of fallas) console.log(`     → ${f}`);
  }
}

const ok = aCorrer.length - fallados.length;
const segundos = ((Date.now() - t0) / 1000).toFixed(1);

console.log('─'.repeat(78));
for (const [g, v] of Object.entries(porGrupo).sort()) {
  console.log(`  ${g.padEnd(18)} ${v.ok}/${v.total}`);
}
const motores = Object.entries(motoresUsados).sort((a, b) => b[1] - a[1]);
console.log(`\nMotor real: ${motores.map(([m, n]) => `${m} ${n}`).join(' · ')}`);
if (motores.length > 1) {
  console.log(
    '\n⚠️  CORRIDA MEZCLADA: algunos casos los parseó un motor distinto del pedido\n' +
    '   (parse() cae al mock ante cualquier error). Este número no es atribuible\n' +
    '   a un solo motor; revisá los warnings de arriba antes de reportarlo.',
  );
}

console.log(`\n${ok}/${aCorrer.length} correctos (${((100 * ok) / aCorrer.length).toFixed(0)} %) en ${segundos} s`);

if (fallados.length > 0) {
  console.log(`\nRepetir uno solo:  npm run eval -- --caso=${fallados[0].c.id}`);
}

// exitCode en vez de exit(): exit() corta el proceso sin vaciar stdout y por
// npm o CI se puede comer el resumen justo cuando falla.
//
// Sin --strict siempre sale 0: un motor con modelo no acierta siempre y no
// tiene sentido romper un flujo por eso salvo que se pida explícitamente.
process.exitCode = strict && fallados.length > 0 ? 1 : 0;
