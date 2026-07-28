// Transporte de WhatsApp (Meta Cloud API): verificación de firma, parseo del
// envelope y envío saliente.
//
// Es un adaptador puro de transporte: importa config y phone, NUNCA repo ni
// pipeline. server.ts sigue siendo el único lugar que conecta los dos lados.
import { createHmac, timingSafeEqual } from 'node:crypto';
import { config } from './config.ts';
import { toWaId } from './phone.ts';

// --- Entrada: firma ---------------------------------------------------------

/**
 * Verifica el header `X-Hub-Signature-256` de Meta sobre los BYTES CRUDOS del
 * body (antes del JSON.parse: re-serializar cambiaría el orden de claves y el
 * escapado unicode, y el HMAC no coincidiría nunca).
 *
 * La clave es el App Secret de la app, NO el access token. Falla cerrado: sin
 * META_APP_SECRET no se acepta ningún webhook.
 */
export function verificarFirma(raw: Buffer, header: string | undefined): boolean {
  if (!config.metaAppSecret || !header) return false;
  const esperado = 'sha256=' + createHmac('sha256', config.metaAppSecret).update(raw).digest('hex');
  const a = Buffer.from(esperado, 'utf8');
  const b = Buffer.from(header, 'utf8');
  // timingSafeEqual tira RangeError si difieren en longitud: sin este guard un
  // header corto crashea el handler en vez de devolver 401.
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

// --- Entrada: envelope ------------------------------------------------------

export interface MensajeEntrante {
  waMessageId: string;   // msg.id ('wamid.…'); '' si falta (curl de prueba)
  from: string;          // wa_id TAL CUAL lo mandó Meta — es a donde se responde
  tipo: string;          // 'text' | 'audio' | 'image' | …
  texto: string;         // '' si no es texto
  timestamp: string;
}

/**
 * Extrae los mensajes entrantes del envelope de Meta.
 *
 * Ignora `change.value.statuses`: los acuses (sent/delivered/read) llegan al
 * mismo endpoint y no son mensajes. Cada mensaje que enviamos genera ~3, así que
 * confundirlos multiplicaría el trabajo por cuatro.
 */
export function extraerEntrantes(payload: any): MensajeEntrante[] {
  const out: MensajeEntrante[] = [];
  for (const entry of payload?.entry ?? []) {
    for (const change of entry?.changes ?? []) {
      for (const msg of change?.value?.messages ?? []) {
        out.push({
          waMessageId: msg?.id ?? '',
          from: msg?.from ?? '',
          tipo: msg?.type ?? 'text',
          texto: msg?.text?.body ?? '',
          timestamp: msg?.timestamp ?? '',
        });
      }
    }
  }
  return out;
}

// --- Salida: envío ----------------------------------------------------------

export interface ResultadoEnvio {
  ok: boolean;
  id?: string;
  error?: string;
}

const MAX_TEXTO = 4096;      // límite de WhatsApp para mensajes de texto
const TIMEOUT_MS = 10_000;

/**
 * Envía un texto por la Cloud API.
 *
 * `to` debe ser el wa_id que mandó Meta, NO el teléfono guardado en la DB: la
 * allow-list de los números de test hace match exacto, y devolverle a Meta su
 * propio identificador esquiva el problema del 9 argentino (error 131030).
 */
export async function enviarTexto(
  to: string, cuerpo: string, responderA?: string,
): Promise<ResultadoEnvio> {
  const faltan: string[] = [];
  if (!config.metaAccessToken) faltan.push('META_ACCESS_TOKEN');
  if (!config.metaPhoneNumberId) faltan.push('META_PHONE_NUMBER_ID');
  if (faltan.length) {
    console.error(`[wa] no se puede enviar: falta ${faltan.join(', ')}`);
    return { ok: false, error: `falta ${faltan.join(', ')}` };
  }

  const url = `https://graph.facebook.com/${config.metaGraphVersion}/${config.metaPhoneNumberId}/messages`;
  const body: Record<string, unknown> = {
    messaging_product: 'whatsapp',
    recipient_type: 'individual',
    to: toWaId(to),   // AR: saca el 9 que Meta manda en el `from` (si no, 131030)
    type: 'text',
    text: { preview_url: false, body: cuerpo.slice(0, MAX_TEXTO) },
  };
  // Engancha la respuesta al mensaje original en el chat: hace mucho más claro
  // el ida y vuelta de la confirmación en el celular.
  if (responderA) body.context = { message_id: responderA };

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.metaAccessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const json = (await res.json().catch(() => ({}))) as any;

    if (!res.ok) {
      // Loguear el código de Meta es lo que convierte 20 minutos de debug en 20
      // segundos. Los habituales: 131030 (destinatario fuera de la allow-list),
      // 190 (token vencido), 100 (phone_number_id mal), 131047 (fuera de 24 h).
      const e = json?.error ?? {};
      const detalle = e.error_data?.details ? ` — ${e.error_data.details}` : '';
      console.error(`[wa] error de Meta ${res.status}: código ${e.code ?? '?'} · ${e.message ?? 'sin mensaje'}${detalle}`);
      return { ok: false, error: `${e.code ?? res.status}: ${e.message ?? 'error'}` };
    }
    console.log(`[wa] → respuesta enviada a ${toWaId(to)} (id ${json?.messages?.[0]?.id?.slice(0, 24) ?? '?'})`);
    return { ok: true, id: json?.messages?.[0]?.id };
  } catch (err) {
    // Nunca loguear headers ni el access token.
    console.error('[wa] fallo de red enviando a Meta:', (err as Error).message);
    return { ok: false, error: (err as Error).message };
  }
}
