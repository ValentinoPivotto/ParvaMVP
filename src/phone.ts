// Normalización de teléfonos (pura, sin I/O).
//
// El problema real: el `wa_id` que manda Meta para números argentinos suele
// OMITIR el 9 de móvil (un +54 9 11 1234-5678 llega como "541112345678"), y la
// dirección del quirk no está documentada de forma confiable. Por eso no se
// adivina cuál forma llega: se buscan TODAS las variantes en la DB y se responde
// siempre al `from` exacto que mandó Meta (ver whatsapp.ts).
const AR = '54';

/** Solo dígitos. Tolera '+', espacios, guiones, paréntesis y prefijo '00'. */
export function soloDigitos(raw: string): string {
  const d = (raw ?? '').replace(/\D/g, '');
  return d.startsWith('00') ? d.slice(2) : d;
}

/** ¿Es un móvil AR al que le falta el 9? (54 + 10 dígitos, sin 9 adelante) */
function esArSin9(d: string): boolean {
  return d.startsWith(AR) && d[2] !== '9' && d.length === 12;
}

/**
 * Forma canónica E.164 con '+'. Para móviles AR agrega el 9 si falta:
 *   '541112345678'   -> '+5491112345678'
 *   '5491112345678'  -> '+5491112345678'
 *   '+5491100000001' -> '+5491100000001'   (los sembrados ya son canónicos)
 */
export function normalizeTelefono(raw: string): string {
  const d = soloDigitos(raw);
  if (!d) return '';
  return '+' + (esArSin9(d) ? AR + '9' + d.slice(2) : d);
}

/**
 * Formas candidatas para buscar en `usuario.telefono`, en orden de prioridad y
 * sin duplicados: tal cual → con '+' → canónica AR-con-9 → AR-sin-9.
 *
 * El orden importa: un teléfono ya canónico (como los sembrados) matchea en la
 * primera consulta, así el simulador web se comporta igual que antes.
 */
export function phoneVariants(raw: string): string[] {
  const d = soloDigitos(raw);
  if (!d) return [];

  const out: string[] = [];
  const push = (v: string) => { if (v && !out.includes(v)) out.push(v); };

  push(raw);            // tal cual vino (cubre los sembrados con '+')
  push('+' + d);        // sin separadores
  push(normalizeTelefono(d));

  // Variante sin el 9: solo para AR móvil ya canónico (54 9 + 10 dígitos).
  if (d.startsWith(AR + '9') && d.length === 13) push('+' + AR + d.slice(3));

  return out;
}

/**
 * Forma del `wa_id` para ENVIAR por la Cloud API.
 *
 * Meta entrega el `from` de los móviles argentinos CON el 9, pero tanto la lista
 * de autorizados del número de prueba como el wa_id canónico de AR lo quieren
 * SIN el 9. Responder al `from` tal cual da error 131030; sacando el 9, entra.
 *   '5491178310248' -> '541178310248'
 * Solo toca AR móvil (549 + 10 dígitos); cualquier otro número queda igual.
 */
export function toWaId(raw: string): string {
  const d = soloDigitos(raw);
  if (d.startsWith(AR + '9') && d.length === 13) return AR + d.slice(3);
  return d;
}
