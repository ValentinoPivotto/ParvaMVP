"""Estado completo del dashboard para un tenant."""
from typing import Any

from ..repository import repo
from ..repository.db import transaccion
from .margin import margen_por_lote


def build_state(productor_id: Any) -> dict[str, Any] | None:
    # Las lecturas van juntas: el dashboard tiene que mostrar una foto
    # coherente. Si entra un mensaje del bot en el medio, el panel podría
    # mostrar el movimiento nuevo y el stock viejo hasta el próximo refresco.
    with transaccion():
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
