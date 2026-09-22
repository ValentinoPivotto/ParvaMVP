"""Export del "spreadsheet" a CSV (la planilla del productor, exportable)."""
import re
from typing import Any

from ..formato import texto_numero
from ..repository.repo import list_hacienda, list_movimientos
from .margin import margen_por_lote

_ESCAPAR = re.compile(r'[",\n]')


def _fila(vals: list[Any]) -> str:
    salida = []
    for v in vals:
        # Un NULL sale vacío y un número con `texto_numero`: los montos salen
        # de columnas REAL, y con `str()` un 1200000 se escribiría "1200000.0".
        s = texto_numero(v) if isinstance(v, (int, float)) or v is None else str(v)
        salida.append('"' + s.replace('"', '""') + '"' if _ESCAPAR.search(s) else s)
    return ','.join(salida)


def export_csv(productor_id: Any, sheet: str) -> tuple[str, str]:
    """Devuelve (filename, content)."""
    if sheet == 'hacienda':
        rows = list_hacienda(productor_id)
        out = [_fila(['categoria', 'cantidad'])]
        for r in rows:
            out.append(_fila([r['categoria'], r['cantidad']]))
        return 'hacienda.csv', '\n'.join(out)

    if sheet == 'margenes':
        margenes = margen_por_lote(productor_id)
        out = [_fila(['lote', 'uso', 'ventas', 'costos', 'margen', 'confiable', 'razon'])]
        for r in margenes:
            out.append(_fila([r.lote_nombre, r.uso, r.ventas, r.costos, r.margen,
                              'sí' if r.confiable else 'no', r.razon if r.razon is not None else '']))
        return 'margenes.csv', '\n'.join(out)

    # default: movimientos
    rows = list_movimientos(productor_id)
    out = [_fila(['fecha', 'tipo', 'lote', 'producto', 'cantidad', 'unidad', 'monto', 'moneda', 'categoria', 'origen'])]
    for r in rows:
        out.append(_fila([r['fecha'], r['tipo'], r['lote_nombre'], r['producto'], r['cantidad'],
                          r['unidad'], r['monto'], r['moneda'], r['categoria'], r['origen']]))
    return 'movimientos.csv', '\n'.join(out)
