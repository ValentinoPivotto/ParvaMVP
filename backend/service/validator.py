"""Validator: reglas de negocio + permisos por rol + umbral de confianza.

Decide si un registro se persiste, se rechaza por permiso, o pide confirmación.
"""
from dataclasses import dataclass

from ..config import config
from ..types import Rol
from .normalizer import Normalized
from .permissions import puede_crear


@dataclass
class Validation:
    ok: bool                  # listo para persistir
    needs_confirmation: bool  # ambiguo / baja confianza => preguntar
    denied: bool              # sin permiso por rol
    motivo: str               # mensaje para el usuario


def _campos_requeridos(n: Normalized) -> str | None:
    rt = n.record_type
    if rt == 'insumo':
        if not n.producto:
            return 'no entendí qué insumo'
        if n.cantidad is None and n.monto is None:
            return 'no entendí cantidad ni monto'
        return None
    if rt == 'gasto':
        if n.monto is None:
            return 'no entendí el monto del gasto'
        return None
    if rt == 'venta':
        if n.monto is None:
            return 'no entendí el monto de la venta'
        return None
    if rt == 'labor':
        return None   # la labor puede no tener monto
    if rt == 'evento_hacienda':
        if not n.categoria:
            return 'no entendí la categoría de hacienda'
        if n.cantidad is None:
            return 'no entendí la cantidad de animales'
        if not n.evento_tipo:
            return 'no entendí qué pasó con los animales'
        return None
    if rt == 'evento_sanitario':
        if not n.producto:
            return 'no entendí el producto sanitario'
        return None
    return 'tipo de registro desconocido'


def validate(n: Normalized, rol: Rol, confidence: float) -> Validation:
    # 1) Permiso por rol (guardrail).
    if not puede_crear(rol, n.record_type):
        return Validation(ok=False, needs_confirmation=False, denied=True,
                          motivo=f'Tu rol ({rol}) no puede registrar {n.record_type}.')
    # 2) Lote mencionado pero no resuelto => ambiguo.
    if n.lote_ref and not n.lote_resuelto:
        return Validation(ok=False, needs_confirmation=True, denied=False,
                          motivo=f'no encontré el lote "{n.lote_ref}"')
    # 3) Campos requeridos.
    falta = _campos_requeridos(n)
    if falta:
        return Validation(ok=False, needs_confirmation=True, denied=False, motivo=falta)
    # 4) Confianza por debajo del umbral => confirmar antes de guardar.
    if confidence < config.confidence_threshold:
        return Validation(ok=False, needs_confirmation=True, denied=False,
                          motivo='no estoy seguro de haber entendido bien')
    return Validation(ok=True, needs_confirmation=False, denied=False, motivo='ok')
