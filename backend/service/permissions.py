"""Matriz de permisos por rol (guardrail). Es la respuesta de §5.4 del spec."""
from ..types import QueryMetric, RecordType, Rol

PUEDE_CREAR: dict[str, list[str]] = {
    'owner': ['insumo', 'labor', 'gasto', 'venta', 'evento_hacienda', 'evento_sanitario'],
    # El gestor de campo carga la operación, pero NO ventas.
    'gestor_campo': ['insumo', 'labor', 'gasto', 'evento_hacienda', 'evento_sanitario'],
}

PUEDE_CONSULTAR: dict[str, list[str]] = {
    'owner': ['stock_animal', 'margen', 'gasto_total', 'venta_total'],
    # El gestor no ve márgenes ni ventas (info económica sensible).
    'gestor_campo': ['stock_animal', 'gasto_total'],
}


def puede_crear(rol: Rol, rt: RecordType) -> bool:
    return rt in PUEDE_CREAR[rol]


def puede_consultar(rol: Rol, metric: QueryMetric) -> bool:
    return metric in PUEDE_CONSULTAR[rol]
