// Diagnóstico del setup de Meta Cloud API.
//
//   npm run check-meta
//   npm run check-meta -- <WABA_ID>
//
// Contesta las tres preguntas detrás de "el número figura Conectado pero el bot
// no responde": ¿a qué número apunta el .env?, ¿en qué WABA está?, ¿la app está
// suscrita a ESA WABA? Cada falla imprime el arreglo concreto, porque los errores
// de Meta son códigos numéricos sin contexto.
import { config } from './config.ts';

const G = `https://graph.facebook.com/${config.metaGraphVersion}`;

// El mismo código de Meta significa cosas distintas según qué nodo pediste, así
// que los hints van por contexto. El 100 sobre una WABA es casi siempre haber
// pasado un phone_number_id, y el hint genérico decía justo lo contrario.
const HINTS = {
  numero: {
    100: 'ese ID no es un phone_number_id (¿pegaste el número de teléfono, o un WABA ID?)',
    190: 'access token vencido o revocado — el temporal del panel dura 24 h',
    200: 'el token no alcanza este número — creá uno de System User con la WABA asignada',
    133010: 'el número no está registrado en la Cloud API — falta el paso del PIN',
  },
  waba: {
    100: 'ese ID no es una WABA — un phone_number_id no tiene edge `phone_numbers`',
    190: 'access token vencido o revocado — el temporal del panel dura 24 h',
    200: 'el token no alcanza esta WABA — creá uno de System User con la WABA asignada',
  },
} as const;

type Contexto = keyof typeof HINTS;

function hint(ctx: Contexto, code?: number): string {
  const h = code ? (HINTS[ctx] as Record<number, string>)[code] : undefined;
  return h ? `\n     → ${h}` : '';
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

// --- 2. El número al que apunta el .env -------------------------------------
const num = await graph(`${config.metaPhoneNumberId}?fields=display_phone_number,verified_name,quality_rating,code_verification_status`);
if (!num.ok) {
  console.error(`✗ META_PHONE_NUMBER_ID=${config.metaPhoneNumberId} — ${num.message} (código ${num.code ?? '?'})${hint('numero', num.code)}\n`);
  process.exit(1);
}
const d = num.data;
console.log(`✓ META_PHONE_NUMBER_ID=${config.metaPhoneNumberId}`);
console.log(`  ${d.display_phone_number} · nombre "${d.verified_name}" · calidad ${d.quality_rating ?? 'n/d'} · ${d.code_verification_status ?? 'n/d'}`);

const esTest = String(d.display_phone_number ?? '').replace(/[^0-9+]/g, '').startsWith('+1555');
if (esTest) {
  problemas.push(
    `El .env apunta al número de TEST (${d.display_phone_number}), no al tuyo.\n` +
    '     Sólo habla con los 5 destinatarios allow-listeados y no se puede renombrar.\n' +
    '     Cambiá META_PHONE_NUMBER_ID por el ID de tu número y volvé a correr esto.',
  );
}

// --- 3. La WABA que contiene ese número -------------------------------------
// Por CLI/env, o preguntándole a Meta a qué WABA llega el token.
let wabas: string[] = [];
const argWaba = (process.argv[2] ?? process.env.META_WABA_ID ?? '').trim();

if (argWaba) {
  // Confusión habitual: pasar el phone_number_id donde va el WABA ID. Son dos
  // IDs numéricos largos indistinguibles a ojo, así que lo detectamos.
  const quizasNumero = await graph(`${argWaba}?fields=display_phone_number`);
  if (quizasNumero.ok && quizasNumero.data?.display_phone_number) {
    problemas.push(
      `${argWaba} es un phone_number_id (${quizasNumero.data.display_phone_number}), no un WABA ID.\n` +
      '     Ese va en META_PHONE_NUMBER_ID. El WABA ID está en WhatsApp Manager →\n' +
      '     Configuración de la cuenta, o lo deduce solo si cargás META_APP_ID en .env.',
    );
  } else {
    wabas = [argWaba];
  }
}

if (!wabas.length && !argWaba) {
  // debug_token necesita un app access token (`APP_ID|APP_SECRET`); con el token
  // de usuario solo, Meta devuelve los scopes vacíos y no se deduce nada.
  if (config.metaAppId && config.metaAppSecret) {
    const app = encodeURIComponent(`${config.metaAppId}|${config.metaAppSecret}`);
    const dbg = await graph(`debug_token?input_token=${encodeURIComponent(config.metaAccessToken)}&access_token=${app}`);
    if (!dbg.ok) {
      console.log(`  · No pude inspeccionar el token: ${dbg.message}`);
    } else {
      const info = dbg.data?.data ?? {};
      const exp = info.expires_at ? new Date(info.expires_at * 1000) : null;
      if (info.expires_at === 0) console.log('✓ Token permanente (no vence)');
      else if (exp) console.log(`${exp > new Date() ? '✓' : '✗'} Token vence ${exp.toLocaleString('es-AR')}`);
      wabas = (info.granular_scopes ?? []).find((s: any) => s.scope === 'whatsapp_business_messaging')?.target_ids ?? [];
      if (wabas.length) console.log(`✓ WABA que alcanza el token: ${wabas.join(', ')}`);
    }
  } else {
    console.log('  · Para deducir la WABA sola, cargá META_APP_ID en .env (Settings → Basic).');
  }
}

if (!wabas.length) {
  console.log('\n⚠ Sin WABA que chequear: salteo la suscripción del webhook.');
  console.log('  Corré:  npm run check-meta -- <WABA_ID>   (o cargá META_APP_ID en .env)');
} else {
  let dueña: string | null = null;

  for (const waba of wabas) {
    const nums = await graph(`${waba}/phone_numbers?fields=id,display_phone_number,verified_name`);
    if (!nums.ok) {
      console.log(`  · WABA ${waba}: no la puedo leer — ${nums.message}${hint('waba', nums.code)}`);
      continue;
    }
    const lista = nums.data?.data ?? [];
    const tiene = lista.some((n: any) => n.id === config.metaPhoneNumberId);
    if (tiene) dueña = waba;
    console.log(`  · WABA ${waba}: ${lista.length} número(s)${tiene ? ' ← contiene el del .env' : ''}`);
    for (const n of lista) {
      console.log(`      ${n.id === config.metaPhoneNumberId ? '▸' : ' '} ${n.id}  ${n.display_phone_number}  "${n.verified_name}"`);
    }
  }

  if (!dueña) {
    problemas.push(`Ninguna WABA visible contiene el número del .env (${config.metaPhoneNumberId}). Si tenés más de una WABA, pasá la otra: npm run check-meta -- <WABA_ID>`);
  } else {
    // --- 4. La suscripción, que es POR WABA y no se hereda de la de test -----
    const subs = await graph(`${dueña}/subscribed_apps`);
    const apps = subs.data?.data ?? [];
    if (!subs.ok) {
      problemas.push(`No pude leer subscribed_apps de la WABA ${dueña}: ${subs.message}`);
    } else if (!apps.length) {
      problemas.push(
        `La app NO está suscrita a la WABA ${dueña} — por eso no llega ni un mensaje. Arreglo:\n` +
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
