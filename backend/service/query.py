"""Consultas de lectura del bot ("¿cuántos terneros tengo?", "¿cuál es el margen?").

Respeta permisos por rol (el gestor no ve margen ni ventas).
"""
from dataclasses import dataclass
from typing import Any, get_args

from ..formato import pesos as fmt
from ..repository.db import transaccion
from ..repository.repo import find_lote_by_ref, gasto_total, list_hacienda, venta_total
from ..types import ParsedQuery, QueryMetric, Rol
from .margin import margen_por_lote
from .permissions import puede_consultar


@dataclass
class QueryAnswer:
    ok: bool
    text: str
    data: Any = None


def answer_query(productor_id: Any, rol: Rol, q: ParsedQuery) -> QueryAnswer:
    # Igual que el dashboard: la respuesta sale de varias consultas y tiene que
    # ser coherente entre sí (un margen mezclando dos fotos miente).
    with transaccion():
        return _responder(productor_id, rol, q)


def _responder(productor_id: Any, rol: Rol, q: ParsedQuery) -> QueryAnswer:
    metric = q.get('metric')
    categoria_animal = q.get('categoriaAnimal')
    lote_ref = q.get('loteRef')

    # Primero si la métrica existe: contestar "tu rol no puede consultar
    # lo_que_sea" manda a buscar un problema de permisos que no hay.
    if metric not in get_args(QueryMetric):
        return QueryAnswer(ok=False, text='No pude responder esa consulta.')
    if not puede_consultar(rol, metric):
        return QueryAnswer(ok=False, text=f'🚫 Tu rol ({rol}) no puede consultar {metric}.')

    if metric == 'stock_animal':
        filas = [h for h in list_hacienda(productor_id) if not categoria_animal or h['categoria'] == categoria_animal]
        if len(filas) == 0:
            return QueryAnswer(ok=True, text=(
                f'No tenés {categoria_animal} registrados.' if categoria_animal else 'No hay hacienda registrada.'))
        total = sum(h['cantidad'] for h in filas)
        if categoria_animal:
            return QueryAnswer(ok=True, text=f'Tenés {total} {categoria_animal}.', data=filas)
        detalle = ', '.join(f'{h["cantidad"]} {h["categoria"]}' for h in filas)
        return QueryAnswer(ok=True, text=f'Stock actual: {detalle}. Total {total} cabezas.', data=filas)

    if metric == 'margen':
        todos = margen_por_lote(productor_id)
        if lote_ref:
            l = find_lote_by_ref(productor_id, lote_ref)
            m = next((x for x in todos if x.lote_id == l['id']), None) if l else None
            if not m:
                return QueryAnswer(ok=True, text=f'No encontré el lote "{lote_ref}".')
            if not m.confiable:
                return QueryAnswer(ok=True, data=m, text=(
                    f'El {m.lote_nombre} todavía no tiene margen confiable ({m.razon}). '
                    f'Costos cargados: {fmt(m.costos)}.'))
            return QueryAnswer(ok=True, data=m, text=(
                f'Margen del {m.lote_nombre}: {fmt(m.margen)} '
                f'(ventas {fmt(m.ventas)} − costos {fmt(m.costos)}).'))
        conf = [m for m in todos if m.confiable]
        if len(conf) == 0:
            return QueryAnswer(ok=True, data=todos,
                               text='Todavía no hay márgenes confiables (faltan ventas o costos cargados).')
        txt = ' · '.join(f'{m.lote_nombre}: {fmt(m.margen)}' for m in conf)
        return QueryAnswer(ok=True, text=f'Márgenes por lote: {txt}.', data=todos)

    if metric == 'gasto_total':
        return QueryAnswer(ok=True, text=f'Gasto total registrado: {fmt(gasto_total(productor_id))}.')

    if metric == 'venta_total':
        return QueryAnswer(ok=True, text=f'Ventas totales registradas: {fmt(venta_total(productor_id))}.')

    return QueryAnswer(ok=False, text='No pude responder esa consulta.')
