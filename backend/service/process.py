"""Orquestador del bot.

Implementa el flujo del spec (§5.1):
  raw_message → transcribe → parse → normalize → validate → persist
con confirmación ante baja confianza/ambigüedad y permisos por rol.
"""
import json
from typing import Any

from ..jscompat import a_json
from ..jscompat import pesos as fmt
from ..jscompat import texto_js, texto_numero
from ..repository import repo
from ..types import ProcessResult, Sender
from .normalizer import Normalized, normalize
from .parser import parse
from .query import answer_query
from .transcriber import transcribe
from .validator import validate


def _primary_campo_id(productor_id: Any) -> int | None:
    campos = repo.list_campos(productor_id)
    return campos[0]['id'] if campos else None


def _resumen(n: Normalized) -> str:
    partes: list[str] = [n.record_type]
    if n.producto or n.labor_tipo:
        partes.append(str(n.producto if n.producto is not None else n.labor_tipo))
    if n.cantidad is not None:
        partes.append(f'{texto_numero(n.cantidad)}{" " + n.unidad if n.unidad else ""}')
    if n.categoria:
        partes.append(n.categoria)
    if n.monto is not None:
        partes.append(fmt(n.monto))
    if n.lote_ref:
        partes.append(f'lote {n.lote_ref}')
    return ' · '.join(partes)


def _confirm_txt(n: Normalized) -> str:
    rt = n.record_type
    if rt == 'insumo':
        cant = f'{texto_numero(n.cantidad)}{" " + n.unidad if n.unidad else ""} de ' if n.cantidad is not None else ''
        por = f' por {fmt(n.monto)}' if n.monto is not None else ''
        en = f' en el lote {n.lote_ref}' if n.lote_ref else ''
        return f'Registré {cant}{texto_js(n.producto)}{por}{en}.'
    if rt == 'labor':
        en = f' en el lote {n.lote_ref}' if n.lote_ref else ''
        monto = f' ({fmt(n.monto)})' if n.monto is not None else ''
        return f'Registré la labor "{n.labor_tipo if n.labor_tipo is not None else "labor"}"{en}{monto}.'
    if rt == 'gasto':
        detalle = f' ({n.producto})' if n.producto else ''
        return f'Registré un gasto de {fmt(n.monto if n.monto is not None else 0)}{detalle}.'
    if rt == 'venta':
        de = f' de {n.producto}' if n.producto else ''
        return f'Registré una venta{de} por {fmt(n.monto if n.monto is not None else 0)}.'
    return 'Registrado.'


def _persistir(sender: Sender, n: Normalized) -> str:
    campo_id = _primary_campo_id(sender.productor_id)
    rt = n.record_type
    if rt in ('insumo', 'labor', 'gasto', 'venta'):
        repo.insert_movimiento(
            productor_id=sender.productor_id, tipo=rt, lote_id=n.lote_id,
            fecha=n.fecha, producto=n.producto if n.producto is not None else n.labor_tipo,
            cantidad=n.cantidad, unidad=n.unidad, monto=n.monto,
            categoria=n.categoria, descripcion=n.descripcion,
            origen='bot', created_by=sender.usuario_id,
        )
        return f'✅ {_confirm_txt(n)}'
    if rt == 'evento_hacienda':
        repo.insert_evento_hacienda(
            productor_id=sender.productor_id, campo_id=campo_id, tipo=n.evento_tipo,
            categoria=n.categoria, cantidad=n.cantidad,
            monto=n.monto, fecha=n.fecha, origen='bot', created_by=sender.usuario_id,
        )
        return f'✅ Registré {texto_js(n.evento_tipo)} de {texto_js(n.cantidad)} {texto_js(n.categoria)}. Stock actualizado.'
    if rt == 'evento_sanitario':
        repo.insert_evento_sanitario(
            productor_id=sender.productor_id, campo_id=campo_id, producto=n.producto,
            categoria=n.categoria, cantidad=n.cantidad,
            fecha=n.fecha, origen='bot', created_by=sender.usuario_id,
        )
        cant = f' ({texto_numero(n.cantidad)})' if n.cantidad is not None else ''
        return f'✅ Registré sanidad: {texto_js(n.producto)}{cant}.'
    return 'Registrado.'


def process_message(
    sender: Sender, texto_entrada: str, wa_message_id: str | None = None,
) -> ProcessResult | None:
    """Procesa un mensaje entrante. Devuelve `None` si `wa_message_id` ya fue procesado
    (reintento de Meta), o sea: el mensaje ya se contestó y hay que ignorarlo.
    """
    texto = transcribe(texto=texto_entrada)
    raw_id = repo.insert_raw_message(sender.productor_id, sender.usuario_id, texto, wa_message_id)
    if raw_id is None:
        return None
    parsed = parse(texto)

    # 1) Confirmación de un pendiente
    if parsed.intent == 'confirm':
        repo.set_raw_estado(raw_id, 'confirmed')
        pend = repo.get_last_pending(sender.productor_id, sender.usuario_id)
        if not pend or not pend['parsed_json']:
            return ProcessResult(reply='No tengo nada pendiente para confirmar.', intent='confirm',
                                 status='unknown', confidence=parsed.confidence)
        norm = Normalized.from_json(json.loads(pend['parsed_json']))
        reply = _persistir(sender, norm)
        repo.set_raw_estado(pend['id'], 'confirmed')
        return ProcessResult(reply=reply, intent='confirm', status='confirmed', confidence=parsed.confidence)

    # 2) Consulta
    if parsed.intent == 'query' and parsed.query:
        query_json = a_json({k: v for k, v in parsed.query.items() if v is not None})
        repo.update_raw_message(raw_id, 'query', None, query_json, parsed.confidence, 'confirmed')
        ans = answer_query(sender.productor_id, sender.rol, parsed.query)
        return ProcessResult(reply=ans.text, intent='query', status='query_answer',
                             confidence=parsed.confidence, detail=ans.data)

    # 3) Alta de registro
    if parsed.intent == 'create_record' and parsed.record_type:
        norm = normalize(parsed, sender.productor_id)
        val = validate(norm, sender.rol, parsed.confidence)
        norm_json = a_json(norm.to_json())

        if val.denied:
            repo.update_raw_message(raw_id, 'create_record', norm.record_type, norm_json,
                                    parsed.confidence, 'discarded')
            return ProcessResult(reply=f'🚫 {val.motivo}', intent='create_record',
                                 status='denied', confidence=parsed.confidence)
        if val.needs_confirmation:
            repo.update_raw_message(raw_id, 'create_record', norm.record_type, norm_json,
                                    parsed.confidence, 'pending')
            return ProcessResult(
                reply=f'🤔 Casi: {val.motivo}. Entendí → {_resumen(norm)}. ¿Lo registro igual? (respondé "sí")',
                intent='create_record', status='needs_confirmation', confidence=parsed.confidence,
            )
        repo.update_raw_message(raw_id, 'create_record', norm.record_type, norm_json,
                                parsed.confidence, 'confirmed')
        reply = _persistir(sender, norm)
        return ProcessResult(reply=reply, intent='create_record', status='created', confidence=parsed.confidence)

    # 4) No entendido
    repo.set_raw_estado(raw_id, 'discarded')
    return ProcessResult(
        reply='No te entendí 🤷. Probá algo como: "compré 200 litros de gasoil para el lote 4" o "¿cuántos terneros tengo?".',
        intent='unknown', status='unknown', confidence=parsed.confidence,
    )
