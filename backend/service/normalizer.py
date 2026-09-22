"""Normalizer: resuelve referencias informales a datos canónicos del tenant
(lote por número/nombre, unidades canónicas, categoría, fecha).
"""
from dataclasses import dataclass
from typing import Any

from ..jscompat import fecha_iso
from ..repository.repo import find_lote_by_ref
from ..types import EventoHaciendaTipo, ParsedIntent, RecordType

UNIDAD_CANON: dict[str, str] = {
    'l': 'L', 'lt': 'L', 'lts': 'L', 'litro': 'L', 'litros': 'L',
    'kg': 'kg', 'kilo': 'kg', 'kilos': 'kg',
    'tn': 'tn', 'ton': 'tn', 'tonelada': 'tn', 'toneladas': 'tn',
    'bolsa': 'bolsa', 'bolsas': 'bolsa',
    'cabeza': 'cabeza', 'cabezas': 'cabeza',
    'unidad': 'u', 'unidades': 'u',
    'ha': 'ha', 'has': 'ha', 'hectarea': 'ha', 'hectareas': 'ha', 'hectárea': 'ha', 'hectáreas': 'ha',
}

@dataclass
class Normalized:
    record_type: RecordType
    lote_id: int | None
    #: False si se mencionó un lote pero no se pudo resolver.
    lote_resuelto: bool
    fecha: str
    lote_ref: str | None = None
    producto: str | None = None
    cantidad: float | None = None
    unidad: str | None = None
    monto: float | None = None
    categoria: str | None = None
    evento_tipo: EventoHaciendaTipo | None = None
    labor_tipo: str | None = None
    descripcion: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Claves camelCase y en el mismo orden que escribía `JSON.stringify` sobre
        el objeto de la versión TS: `parsed_json` queda byte a byte igual, así una
        base existente no muestra dos formatos según quién escribió la fila.
        """
        # (clave, valor, siempre). Los `siempre=False` se omiten cuando no hay
        # valor, que es lo que hacía JSON.stringify con un `undefined`.
        campos: list[tuple[str, Any, bool]] = [
            ('recordType', self.record_type, True),
            ('loteId', self.lote_id, True),
            ('loteRef', self.lote_ref, False),
            ('loteResuelto', self.lote_resuelto, True),
            ('producto', self.producto, False),
            ('cantidad', self.cantidad, False),
            ('unidad', self.unidad, False),
            ('monto', self.monto, False),
            ('categoria', self.categoria, False),
            ('eventoTipo', self.evento_tipo, False),
            ('laborTipo', self.labor_tipo, False),
            ('fecha', self.fecha, True),
            ('descripcion', self.descripcion, False),
        ]
        return {k: v for k, v, siempre in campos if siempre or v is not None}

    @staticmethod
    def from_json(d: dict[str, Any]) -> 'Normalized':
        """Lee un `parsed_json` guardado. Tolera claves ausentes: las filas que
        escribió la versión en TypeScript omitían los campos vacíos.
        """
        return Normalized(
            record_type=d.get('recordType'),
            lote_id=d.get('loteId'),
            lote_resuelto=d.get('loteResuelto', True),
            fecha=d.get('fecha'),
            lote_ref=d.get('loteRef'),
            producto=d.get('producto'),
            cantidad=d.get('cantidad'),
            unidad=d.get('unidad'),
            monto=d.get('monto'),
            categoria=d.get('categoria'),
            evento_tipo=d.get('eventoTipo'),
            labor_tipo=d.get('laborTipo'),
            descripcion=d.get('descripcion'),
        )


def normalize(parsed: ParsedIntent, productor_id: Any) -> Normalized:
    f = parsed.fields
    lote_id: int | None = None
    resuelto = True
    if f.get('loteRef'):
        l = find_lote_by_ref(productor_id, f['loteRef'])
        if l:
            lote_id = l['id']
        else:
            resuelto = False
    unidad = f.get('unidad')
    categoria = f.get('categoriaAnimal')
    return Normalized(
        record_type=parsed.record_type,
        lote_id=lote_id,
        lote_ref=f.get('loteRef'),
        lote_resuelto=resuelto,
        producto=f.get('producto'),
        cantidad=f.get('cantidad'),
        unidad=(UNIDAD_CANON.get(unidad, unidad) if unidad else None),
        monto=f.get('monto'),
        categoria=categoria if categoria is not None else f.get('categoria'),
        evento_tipo=f.get('eventoTipo'),
        labor_tipo=f.get('laborTipo'),
        fecha=f.get('fecha') if f.get('fecha') is not None else fecha_iso(),
        descripcion=f.get('descripcion'),
    )
