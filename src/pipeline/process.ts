// Orquestador del bot. Implementa el flujo del spec (§5.1):
//   raw_message → transcribe → parse → normalize → validate → persist
// con confirmación ante baja confianza/ambigüedad y permisos por rol.
import * as repo from '../repo.ts';
import { transcribe } from './transcriber.ts';
import { parse } from './parser.ts';
import { normalize, type Normalized } from './normalizer.ts';
import { validate } from './validator.ts';
import { answerQuery } from '../services/query.ts';
import type { Sender, ProcessResult } from '../types.ts';

function fmt(n: number): string {
  return '$' + Math.round(n).toLocaleString('es-AR');
}

function primaryCampoId(productorId: number): number | null {
  const campos = repo.listCampos(productorId);
  return campos.length ? campos[0].id : null;
}

function resumen(n: Normalized): string {
  const partes: string[] = [n.recordType];
  if (n.producto || n.laborTipo) partes.push(String(n.producto ?? n.laborTipo));
  if (n.cantidad != null) partes.push(`${n.cantidad}${n.unidad ? ' ' + n.unidad : ''}`);
  if (n.categoria) partes.push(n.categoria);
  if (n.monto != null) partes.push(fmt(n.monto));
  if (n.loteRef) partes.push(`lote ${n.loteRef}`);
  return partes.join(' · ');
}

function confirmTxt(n: Normalized): string {
  switch (n.recordType) {
    case 'insumo': {
      const cant = n.cantidad != null ? `${n.cantidad}${n.unidad ? ' ' + n.unidad : ''} de ` : '';
      return `Registré ${cant}${n.producto}${n.monto != null ? ` por ${fmt(n.monto)}` : ''}${n.loteRef ? ` en el lote ${n.loteRef}` : ''}.`;
    }
    case 'labor':
      return `Registré la labor "${n.laborTipo ?? 'labor'}"${n.loteRef ? ` en el lote ${n.loteRef}` : ''}${n.monto != null ? ` (${fmt(n.monto)})` : ''}.`;
    case 'gasto':
      return `Registré un gasto de ${fmt(n.monto ?? 0)}${n.producto ? ` (${n.producto})` : ''}.`;
    case 'venta':
      return `Registré una venta${n.producto ? ` de ${n.producto}` : ''} por ${fmt(n.monto ?? 0)}.`;
    default:
      return 'Registrado.';
  }
}

function persistir(sender: Sender, n: Normalized): string {
  const campoId = primaryCampoId(sender.productorId);
  switch (n.recordType) {
    case 'insumo':
    case 'labor':
    case 'gasto':
    case 'venta':
      repo.insertMovimiento({
        productorId: sender.productorId, tipo: n.recordType, loteId: n.loteId,
        fecha: n.fecha, producto: n.producto ?? n.laborTipo ?? null,
        cantidad: n.cantidad ?? null, unidad: n.unidad ?? null, monto: n.monto ?? null,
        categoria: n.categoria ?? null, descripcion: n.descripcion ?? null,
        origen: 'bot', createdBy: sender.usuarioId,
      });
      return `✅ ${confirmTxt(n)}`;
    case 'evento_hacienda':
      repo.insertEventoHacienda({
        productorId: sender.productorId, campoId, tipo: n.eventoTipo as any,
        categoria: n.categoria as string, cantidad: n.cantidad as number,
        monto: n.monto ?? null, fecha: n.fecha, origen: 'bot', createdBy: sender.usuarioId,
      });
      return `✅ Registré ${n.eventoTipo} de ${n.cantidad} ${n.categoria}. Stock actualizado.`;
    case 'evento_sanitario':
      repo.insertEventoSanitario({
        productorId: sender.productorId, campoId, producto: n.producto ?? null,
        categoria: n.categoria ?? null, cantidad: n.cantidad ?? null,
        fecha: n.fecha, origen: 'bot', createdBy: sender.usuarioId,
      });
      return `✅ Registré sanidad: ${n.producto}${n.cantidad != null ? ` (${n.cantidad})` : ''}.`;
    default:
      return 'Registrado.';
  }
}

/**
 * Procesa un mensaje entrante. Devuelve `null` si `waMessageId` ya fue procesado
 * (reintento de Meta); el simulador no pasa waMessageId, así que nunca es null.
 */
export async function processMessage(
  sender: Sender, textoEntrada: string, waMessageId?: string | null,
): Promise<ProcessResult | null> {
  const texto = await transcribe({ texto: textoEntrada });
  const rawId = repo.insertRawMessage(sender.productorId, sender.usuarioId, texto, waMessageId);
  if (rawId === null) return null;
  const parsed = await parse(texto);

  // 1) Confirmación de un pendiente
  if (parsed.intent === 'confirm') {
    repo.setRawEstado(rawId, 'confirmed');
    const pend = repo.getLastPending(sender.productorId, sender.usuarioId);
    if (!pend || !pend.parsed_json) {
      return { reply: 'No tengo nada pendiente para confirmar.', intent: 'confirm', status: 'unknown', confidence: parsed.confidence };
    }
    const norm = JSON.parse(pend.parsed_json) as Normalized;
    const reply = persistir(sender, norm);
    repo.setRawEstado(pend.id, 'confirmed');
    return { reply, intent: 'confirm', status: 'confirmed', confidence: parsed.confidence };
  }

  // 2) Consulta
  if (parsed.intent === 'query' && parsed.query) {
    repo.updateRawMessage(rawId, 'query', null, JSON.stringify(parsed.query), parsed.confidence, 'confirmed');
    const ans = answerQuery(sender.productorId, sender.rol, parsed.query);
    return { reply: ans.text, intent: 'query', status: 'query_answer', confidence: parsed.confidence, detail: ans.data };
  }

  // 3) Alta de registro
  if (parsed.intent === 'create_record' && parsed.recordType) {
    const norm = normalize(parsed, sender.productorId);
    const val = validate(norm, sender.rol, parsed.confidence);

    if (val.denied) {
      repo.updateRawMessage(rawId, 'create_record', norm.recordType, JSON.stringify(norm), parsed.confidence, 'discarded');
      return { reply: `🚫 ${val.motivo}`, intent: 'create_record', status: 'denied', confidence: parsed.confidence };
    }
    if (val.needsConfirmation) {
      repo.updateRawMessage(rawId, 'create_record', norm.recordType, JSON.stringify(norm), parsed.confidence, 'pending');
      return {
        reply: `🤔 Casi: ${val.motivo}. Entendí → ${resumen(norm)}. ¿Lo registro igual? (respondé "sí")`,
        intent: 'create_record', status: 'needs_confirmation', confidence: parsed.confidence,
      };
    }
    repo.updateRawMessage(rawId, 'create_record', norm.recordType, JSON.stringify(norm), parsed.confidence, 'confirmed');
    const reply = persistir(sender, norm);
    return { reply, intent: 'create_record', status: 'created', confidence: parsed.confidence };
  }

  // 4) No entendido
  repo.setRawEstado(rawId, 'discarded');
  return {
    reply: 'No te entendí 🤷. Probá algo como: "compré 200 litros de gasoil para el lote 4" o "¿cuántos terneros tengo?".',
    intent: 'unknown', status: 'unknown', confidence: parsed.confidence,
  };
}
