"""Estado completo del dashboard para un tenant."""
from typing import Any

from ..repository import repo
from .margin import margen_por_lote


def build_state(productor_id: Any) -> dict[str, Any] | None:
    productor = repo.get_productor(productor_id)
    if not productor:
        return None
    return {
        'productor': productor,
        'campos': repo.list_campos(productor_id),
        'lotes': repo.list_lotes(productor_id),
        'campanias': repo.list_campanias(productor_id),
        'movimientos': repo.list_movimientos(productor_id),
        'hacienda': repo.list_hacienda(productor_id),
        'sanidad': repo.list_eventos_sanitarios(productor_id),
        'margenes': [m.to_json() for m in margen_por_lote(productor_id)],
        'mensajes': repo.list_raw_messages(productor_id, 15),
    }
