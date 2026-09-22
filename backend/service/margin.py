"""Cálculo de márgenes por lote.

Principio del spec (§4.4): un margen se muestra solo si es confiable; si faltan
ventas o costos, se indica en vez de mentir.
"""
from dataclasses import dataclass
from typing import Any

from ..repository.repo import list_lotes, totales_por_lote


@dataclass
class MargenLote:
    lote_id: int
    lote_nombre: str
    uso: str | None
    ventas: float
    costos: float
    margen: float
    confiable: bool
    razon: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Claves camelCase: son las que lee el frontend (`m.loteNombre`).

        `razon` se omite cuando no hay, igual que hacía `JSON.stringify` con un
        campo `undefined`.
        """
        d: dict[str, Any] = {
            'loteId': self.lote_id,
            'loteNombre': self.lote_nombre,
            'uso': self.uso,
            'ventas': self.ventas,
            'costos': self.costos,
            'margen': self.margen,
            'confiable': self.confiable,
        }
        if self.razon is not None:
            d['razon'] = self.razon
        return d


def margen_por_lote(productor_id: Any) -> list[MargenLote]:
    salida: list[MargenLote] = []
    for l in list_lotes(productor_id):
        totales = totales_por_lote(productor_id, l['id'])
        ventas, costos = totales['ventas'], totales['costos']
        confiable = True
        razon: str | None = None
        if ventas == 0:
            confiable, razon = False, 'sin ventas registradas'
        elif costos == 0:
            confiable, razon = False, 'sin costos registrados'
        salida.append(MargenLote(
            lote_id=l['id'], lote_nombre=l['nombre'], uso=l['uso_actual'],
            ventas=ventas, costos=costos, margen=ventas - costos,
            confiable=confiable, razon=razon,
        ))
    return salida
