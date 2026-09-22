"""Repositorio: única puerta de acceso a los datos.

TODA lectura/escritura va filtrada por productor_id => aislamiento por tenant
(guardrail principal).
"""
from typing import Any

from ..jscompat import texto_numero
from ..phone import phone_variants
from ..types import Sender
from . import db

# --- Identidad / tenant ---------------------------------------------------


def get_sender_by_telefono(telefono: str) -> Sender | None:
    """Busca por variantes (no por match exacto) porque el wa_id argentino de Meta
    suele venir sin el 9 de móvil. Loop ordenado en vez de `IN (...)`: con `IN` el
    motor elegiría arbitrariamente si dos filas matchean variantes distintas.
    """
    sql = """
    SELECT u.id AS usuarioId, u.nombre AS usuarioNombre, u.rol AS rol,
           p.id AS productorId, p.tipo_campo AS tipoCampo
    FROM usuario u JOIN productor p ON p.id = u.productor_id
    WHERE u.telefono = ?
  """
    for variante in phone_variants(telefono):
        fila = db.get(sql, variante)
        if not fila:
            continue
        return Sender(
            productor_id=fila['productorId'],
            tipo_campo=fila['tipoCampo'],
            usuario_id=fila['usuarioId'],
            usuario_nombre=fila['usuarioNombre'],
            rol=fila['rol'],
        )
    return None


def list_productores() -> list[dict[str, Any]]:
    return db.all('SELECT * FROM productor ORDER BY id')


def get_productor(id: Any) -> dict[str, Any] | None:
    return db.get('SELECT * FROM productor WHERE id = ?', id)


# --- Estructura productiva ------------------------------------------------


def list_campos(productor_id: Any) -> list[dict[str, Any]]:
    return db.all('SELECT * FROM campo WHERE productor_id = ? ORDER BY id', productor_id)


def list_lotes(productor_id: Any) -> list[dict[str, Any]]:
    return db.all('SELECT * FROM lote WHERE productor_id = ? ORDER BY id', productor_id)


def list_campanias(productor_id: Any) -> list[dict[str, Any]]:
    return db.all('SELECT * FROM campania WHERE productor_id = ? ORDER BY id', productor_id)


def find_lote_by_ref(productor_id: Any, ref: str) -> dict[str, Any] | None:
    """Resuelve una referencia informal de lote ("4", "lote 4", "norte") a un lote real."""
    r = ref.strip().lower()
    lotes = list_lotes(productor_id)

    def buscar(pred) -> dict[str, Any] | None:
        return next((l for l in lotes if pred(l)), None)

    return (
        buscar(lambda l: str(l['numero'] if l['numero'] is not None else '').lower() == r)
        or buscar(lambda l: str(l['nombre'] if l['nombre'] is not None else '').lower() == r)
        or buscar(lambda l: str(l['nombre'] if l['nombre'] is not None else '').lower() == f'lote {r}')
        or buscar(lambda l: r in str(l['nombre'] if l['nombre'] is not None else '').lower())
        or None
    )


# --- Movimientos ----------------------------------------------------------


def insert_movimiento(
    *,
    productor_id: Any,
    tipo: str,
    fecha: str,
    lote_id: Any = None,
    campania_id: Any = None,
    producto: str | None = None,
    cantidad: float | None = None,
    unidad: str | None = None,
    monto: float | None = None,
    moneda: str = 'ARS',
    categoria: str | None = None,
    descripcion: str | None = None,
    origen: str = 'web',
    created_by: int | None = None,
) -> int:
    info = db.run(
        """
    INSERT INTO movimiento
      (productor_id, tipo, lote_id, campania_id, fecha, producto, cantidad, unidad,
       monto, moneda, categoria, descripcion, origen, created_by)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  """,
        productor_id, tipo, lote_id, campania_id, fecha, producto, cantidad, unidad,
        monto, moneda, categoria, descripcion, origen, created_by,
    )
    id = info.last_insert_rowid
    detalle = f'{tipo} {producto or ""} {texto_numero(monto)}'.strip()
    audit(productor_id, 'movimiento', id, 'create', created_by, origen, detalle)
    return id


def list_movimientos(productor_id: Any) -> list[dict[str, Any]]:
    return db.all(
        """
    SELECT m.*, l.nombre AS lote_nombre
    FROM movimiento m LEFT JOIN lote l ON l.id = m.lote_id
    WHERE m.productor_id = ? ORDER BY m.fecha DESC, m.id DESC
  """,
        productor_id,
    )


# Agregaciones de datos; las reglas de márgenes y permisos viven en service/.
def gasto_total(productor_id: Any) -> float:
    fila = db.get(
        "SELECT COALESCE(SUM(monto),0) AS s FROM movimiento WHERE productor_id=? AND tipo IN ('insumo','labor','gasto')",
        productor_id,
    )
    return fila['s']


def venta_total(productor_id: Any) -> float:
    fila = db.get(
        "SELECT COALESCE(SUM(monto),0) AS s FROM movimiento WHERE productor_id=? AND tipo='venta'",
        productor_id,
    )
    return fila['s']


def _suma_por_lote(productor_id: Any, lote_id: Any, where: str) -> float:
    fila = db.get(
        f'SELECT COALESCE(SUM(monto),0) AS s FROM movimiento WHERE productor_id=? AND lote_id=? AND {where}',
        productor_id, lote_id,
    )
    return fila['s']


def totales_por_lote(productor_id: Any, lote_id: Any) -> dict[str, float]:
    return {
        'ventas': _suma_por_lote(productor_id, lote_id, "tipo='venta'"),
        'costos': _suma_por_lote(productor_id, lote_id, "tipo IN ('insumo','labor','gasto')"),
    }


# --- Hacienda (ganadero) --------------------------------------------------


def list_hacienda(productor_id: Any) -> list[dict[str, Any]]:
    return db.all('SELECT * FROM hacienda WHERE productor_id = ? ORDER BY categoria', productor_id)


def adjust_hacienda(productor_id: Any, campo_id: int | None, categoria: str, delta: float) -> None:
    """Ajusta el stock de una categoría (crea la fila si no existe). delta puede ser + o -."""
    fila = db.get('SELECT * FROM hacienda WHERE productor_id = ? AND categoria = ?', productor_id, categoria)
    if fila:
        nueva = max(0, (fila['cantidad'] or 0) + delta)
        db.run('UPDATE hacienda SET cantidad = ? WHERE id = ?', nueva, fila['id'])
    elif delta > 0:
        db.run(
            'INSERT INTO hacienda (productor_id, campo_id, categoria, cantidad) VALUES (?,?,?,?)',
            productor_id, campo_id, categoria, delta,
        )


def insert_evento_hacienda(
    *,
    productor_id: Any,
    tipo: str,
    categoria: str,
    cantidad: float,
    fecha: str,
    campo_id: int | None = None,
    monto: float | None = None,
    origen: str = 'web',
    created_by: int | None = None,
) -> int:
    info = db.run(
        """
    INSERT INTO evento_hacienda
      (productor_id, campo_id, tipo, categoria, cantidad, monto, fecha, origen, created_by)
    VALUES (?,?,?,?,?,?,?,?,?)
  """,
        productor_id, campo_id, tipo, categoria, cantidad, monto, fecha, origen, created_by,
    )
    # El stock se mueve según el tipo de evento.
    signo = 1 if tipo in ('nacimiento', 'compra') else -1 if tipo in ('muerte', 'venta') else 0
    if signo != 0:
        adjust_hacienda(productor_id, campo_id, categoria, signo * cantidad)
    id = info.last_insert_rowid
    audit(productor_id, 'evento_hacienda', id, 'create', created_by, origen,
          f'{tipo} {texto_numero(cantidad)} {categoria}')
    return id


def insert_evento_sanitario(
    *,
    productor_id: Any,
    fecha: str,
    campo_id: int | None = None,
    producto: str | None = None,
    categoria: str | None = None,
    cantidad: float | None = None,
    origen: str = 'web',
    created_by: int | None = None,
) -> int:
    info = db.run(
        """
    INSERT INTO evento_sanitario
      (productor_id, campo_id, producto, categoria, cantidad, fecha, origen, created_by)
    VALUES (?,?,?,?,?,?,?,?)
  """,
        productor_id, campo_id, producto, categoria, cantidad, fecha, origen, created_by,
    )
    id = info.last_insert_rowid
    audit(productor_id, 'evento_sanitario', id, 'create', created_by, origen,
          f'{producto if producto is not None else "sanidad"} {texto_numero(cantidad)}'.strip())
    return id


def list_eventos_sanitarios(productor_id: Any) -> list[dict[str, Any]]:
    return db.all('SELECT * FROM evento_sanitario WHERE productor_id = ? ORDER BY fecha DESC, id DESC', productor_id)


# --- Mensajes crudos + confirmación ---------------------------------------


def insert_raw_message(
    productor_id: Any, usuario_id: int, texto: str, wa_message_id: str | None = None,
) -> int | None:
    """Inserta el mensaje entrante. Devuelve el id nuevo, o `None` si `wa_message_id`
    ya existía: Meta reintenta los webhooks (entrega at-least-once) y sin esto
    cada reintento duplicaría el registro.

    El índice único de `wa_message_id` es el que arbitra, no un SELECT previo, así
    que no hay ventana de carrera entre dos reintentos concurrentes.
    """
    info = db.run(
        'INSERT OR IGNORE INTO raw_message (productor_id, usuario_id, texto, wa_message_id) VALUES (?,?,?,?)',
        productor_id, usuario_id, texto, wa_message_id,
    )
    # ⚠ Con INSERT OR IGNORE hay que mirar `changes`: en un insert ignorado
    # lastInsertRowid conserva el rowid ANTERIOR, así que devolveríamos el id de
    # otra fila y el pipeline mutaría un raw_message ajeno en silencio.
    if info.changes == 0:
        return None
    return info.last_insert_rowid


def update_raw_message(
    id: int, intent: str, record_type: str | None, parsed_json: str, confidence: float, estado: str,
) -> None:
    db.run(
        """
    UPDATE raw_message SET intent = ?, record_type = ?, parsed_json = ?, confidence = ?, estado = ?
    WHERE id = ?
  """,
        intent, record_type, parsed_json, confidence, estado, id,
    )


def set_raw_estado(id: int, estado: str) -> None:
    db.run('UPDATE raw_message SET estado = ? WHERE id = ?', estado, id)


def get_last_pending(productor_id: Any, usuario_id: int) -> dict[str, Any] | None:
    return db.get(
        """
    SELECT * FROM raw_message
    WHERE productor_id = ? AND usuario_id = ? AND estado = 'pending' AND parsed_json IS NOT NULL
    ORDER BY id DESC LIMIT 1
  """,
        productor_id, usuario_id,
    )


def list_raw_messages(productor_id: Any, limit: int = 20) -> list[dict[str, Any]]:
    return db.all('SELECT * FROM raw_message WHERE productor_id = ? ORDER BY id DESC LIMIT ?', productor_id, limit)


# --- Auditoría ------------------------------------------------------------


def audit(
    productor_id: Any, entidad: str, entidad_id: int | None,
    accion: str, usuario_id: int | None, origen: str | None, detalle: str,
) -> None:
    db.run(
        """
    INSERT INTO audit_log (productor_id, entidad, entidad_id, accion, usuario_id, origen, detalle)
    VALUES (?,?,?,?,?,?,?)
  """,
        productor_id, entidad, entidad_id, accion, usuario_id, origen, detalle,
    )
