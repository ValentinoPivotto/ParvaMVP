// Diagnóstico del setup de Meta Cloud API.
//
//   npm run check-meta
//   npm run check-meta -- <WABA_ID>
//
// Contesta las tres preguntas detrás de "el número figura Conectado pero el bot
// no responde": ¿el token llega a este número?, ¿en qué WABA está?, ¿la app está
// suscrita a ESA WABA? Cada falla imprime el arreglo concreto, porque los errores
// de Meta son códigos numéricos sin contexto.
import { config } from './config.ts';

const G = `https://graph.facebook.com/${config.metaGraphVersion}`;

// Los códigos que aparecen en este flujo, traducidos al arreglo que corresponde.
const HINTS: Record<number, string> = {
  100: 'el ID no existe o no es un phone_number_id (¿pegaste el número de teléfono en vez del ID?)',
  190: 'access token vencido o revocado — el temporal del panel dura 24 h',
  200: 'el token no alcanza este recurso — creá uno de System User con la WABA asignada',
  133010: 'el número no está registrado en la Cloud API — falta el paso del PIN',
};

function hint(code?: number): string {
  return code && HINTS[code] ? `\n     → ${HINTS[code]}` : '';
}

async function graph(path: string): Promise<{ ok: boolean; data?: any; code?: number; message?: string }> {
  try {
    const res = await fetch(`${G}/${path}`, {
      headers: { Authorization: `Bearer ${config.metaAccessToken}` },
      signal: AbortSignal.timeout(10_000),
    });
    const json: any = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, code: json?.error?.code, message: json?.error?.message ?? `HTTP ${res.status}` };
    return { ok: true, data: json };
  } catch (err) {
    return { ok: false, message: (err as Error).message };
  }
}

const problemas: string[] = [];

console.log(`\n🔎 Chequeo de Meta Cloud API (Graph ${config.metaGraphVersion})\n`);

// --- 1. Variables de entorno ------------------------------------------------
const faltantes = [
  ['META_ACCESS_TOKEN', config.metaAccessToken],
  ['META_PHONE_NUMBER_ID', config.metaPhoneNumberId],
  ['META_APP_SECRET', config.metaAppSecret],
].filter(([, v]) => !String(v).trim()).map(([k]) => k as string);

if (faltantes.length) {
  console.error(`✗ Faltan variables en .env: ${faltantes.join(', ')}`);
  console.error('  Copiá .env.example a .env y completalas. Sin token no se puede chequear nada más.\n');
  process.exit(1);
}
console.log(`✓ .env completo · WHATSAPP_MODE=${config.whatsappMode}`);
if (config.whatsappMode !== 'meta') {
  problemas.push('WHATSAPP_MODE no es "meta": el bot va a contestar en el body del webhook, no por WhatsApp.');
}

// --- 2. El número: ¿existe y el token llega? --------------------------------
const num = await graph(`${config.metaPhoneNumberId}?fields=display_phone_number,verified_name,quality_rating,code_verification_status`);
if (!num.ok) {
  console.error(`✗ META_PHONE_NUMBER_ID=${config.metaPhoneNumberId} — ${num.message} (código ${num.code ?? '?'})${hint(num.code)}\n`);
  process.exit(1);
}
const d = num.data;
console.log(`✓ Número ${d.display_phone_number} · nombre "${d.verified_name}" · calidad ${d.quality_rating ?? 'n/d'} · ${d.code_verification_status ?? 'n/d'}`);
if (d.display_phone_number?.startsWith('+1 555')) {
  problemas.push('Estás apuntando al número de TEST: sólo habla con los 5 destinatarios allow-listeados y no se puede renombrar.');
}

// --- 3. La WABA: ¿cuál tiene este número? -----------------------------------
// Por CLI, por env, o deducida de los granular_scopes del token (ahí Meta lista
// las WABA que el token puede tocar).
let wabas: string[] = [process.argv[2] ?? process.env.META_WABA_ID ?? ''].filter(Boolean);

if (!wabas.length) {
  const dbg = await graph(`debug_token?input_token=${encodeURIComponent(config.metaAccessToken)}`);
  const scopes = dbg.data?.data?.granular_scopes ?? [];
  wabas = scopes.find((s: any) => s.scope === 'whatsapp_business_messaging')?.target_ids ?? [];
  if (wabas.length) console.log(`✓ WABA detectadas en el token: ${wabas.join(', ')}`);
}

if (!wabas.length) {
  console.log('\n⚠ No pude deducir la WABA del token.');
  console.log('  Buscá el ID en WhatsApp Manager y volvé a correr:  npm run check-meta -- <WABA_ID>');
} else {
  let dueña: string | null = null;

  for (const waba of wabas) {
    const nums = await graph(`${waba}/phone_numbers?fields=id,display_phone_number,verified_name`);
    if (!nums.ok) { console.log(`  · WABA ${waba}: no la puedo leer — ${nums.message}${hint(nums.code)}`); continue; }

    const lista = nums.data?.data ?? [];
    const tiene = lista.some((n: any) => n.id === config.metaPhoneNumberId);
    if (tiene) dueña = waba;
    console.log(`  · WABA ${waba}: ${lista.length} número(s)${tiene ? ' ← contiene el tuyo' : ''}`);
    for (const n of lista) console.log(`      ${n.id === config.metaPhoneNumberId ? '▸' : ' '} ${n.id}  ${n.display_phone_number}  "${n.verified_name}"`);
  }

  if (!dueña) {
    problemas.push(`Ninguna WABA visible contiene el número ${config.metaPhoneNumberId}. Pasá el WABA ID a mano: npm run check-meta -- <WABA_ID>`);
  } else {
    // --- 4. La suscripción: es POR WABA y no se hereda de la de test ---------
    const subs = await graph(`${dueña}/subscribed_apps`);
    const apps = subs.data?.data ?? [];
    if (!subs.ok) {
      problemas.push(`No pude leer subscribed_apps de la WABA ${dueña}: ${subs.message}`);
    } else if (!apps.length) {
      problemas.push(
        `La app NO está suscrita a la WABA ${dueña} — por eso no llega ni un mensaje a este número. Arreglo:\n` +
        `     curl -X POST "${G}/${dueña}/subscribed_apps" -H "Authorization: Bearer $META_ACCESS_TOKEN"`,
      );
    } else {
      const nombres = apps.map((a: any) => a?.whatsapp_business_api_data?.name ?? a?.id ?? '?').join(', ');
      console.log(`✓ App suscrita a la WABA ${dueña}: ${nombres}`);
    }
  }
}

// --- Veredicto --------------------------------------------------------------
if (!problemas.length) {
  console.log('\n✅ Todo listo. Escribile al número y mirá el log `[wa] ←` en la terminal del server.\n');
} else {
  console.log(`\n⚠ ${problemas.length} cosa(s) para arreglar:\n`);
  for (const p of problemas) console.log(`  • ${p}\n`);
  process.exit(1);
}
