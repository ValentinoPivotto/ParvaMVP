"""Tipos del dominio Parva.

Uniones de strings (`Literal`) y no `Enum`: estos valores viajan como texto
crudo a la base, al JSON de la API y a la salida del modelo, así que un tipo que
hay que envolver y desenvolver en cada frontera sólo agregaría ruido.
"""
from dataclasses import dataclass
from typing import Any, Literal

Rol = Literal['owner', 'gestor_campo']
TipoCampo = Literal['agricola', 'ganadero', 'mixto']

# Movimientos de la "gestión diaria" / económicos.
MovTipo = Literal['insumo', 'labor', 'gasto', 'venta']

# Eventos de hacienda (ganadero).
EventoHaciendaTipo = Literal['nacimiento', 'muerte', 'compra', 'venta', 'traslado']

# Lo que el parser puede entender de un mensaje.
Intent = Literal['create_record', 'query', 'confirm', 'unknown']
RecordType = Literal['insumo', 'labor', 'gasto', 'venta', 'evento_hacienda', 'evento_sanitario']
QueryMetric = Literal['stock_animal', 'margen', 'gasto_total', 'venta_total']

# Los campos que extrae el parser (`ParsedFields` en la versión TS) y la consulta
# (`ParsedQuery`) quedan como dicts: los tres caminos del parser los reciben del
# JSON de un modelo, que puede traer cualquier cosa. Las claves son las mismas:
#   fields: producto, cantidad, unidad, monto, moneda, loteRef, categoriaAnimal,
#           eventoTipo, laborTipo, categoria, fecha, descripcion
#   query:  metric, loteRef, categoriaAnimal
ParsedFields = dict[str, Any]
ParsedQuery = dict[str, Any]


@dataclass
class ParsedIntent:
    """Salida del parser. Mismo shape para los tres caminos (Bedrock, local, mock)."""

    intent: Intent
    record_type: RecordType | None
    fields: ParsedFields
    query: ParsedQuery | None
    confidence: float
    raw_text: str
    #: Motor que produjo este resultado. Un modelo que falla cae al mock en
    #: silencio, así que sin esto no hay forma de saber qué lo parseó.
    motor: Literal['mock', 'bedrock', 'local'] | None = None

    def reemplazar(self, **cambios: Any) -> 'ParsedIntent':
        """Equivalente de `{ ...base, ...cambios }`."""
        return ParsedIntent(**{**self.__dict__, **cambios})


@dataclass
class Sender:
    """Contexto del remitente, ya resuelto por teléfono (aislamiento por tenant)."""

    productor_id: int
    tipo_campo: TipoCampo
    usuario_id: int
    usuario_nombre: str
    rol: Rol


@dataclass
class ProcessResult:
    """Resultado de procesar un mensaje: lo que el bot responde + metadata para la UI."""

    reply: str
    intent: Intent
    status: Literal['created', 'needs_confirmation', 'confirmed', 'denied', 'query_answer', 'unknown']
    confidence: float
    detail: Any = None
