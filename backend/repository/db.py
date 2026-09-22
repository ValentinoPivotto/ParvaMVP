"""Capa de base de datos: sqlite3 de la stdlib, sin dependencias.

En producción se cambiaría por Postgres/Supabase; el repositorio (repo.py)
aísla el resto del código de este detalle.
"""
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from ..config import config

os.makedirs(os.path.dirname(os.path.abspath(config.db_path)), exist_ok=True)

# `isolation_level=None` = autocommit, que es como se comportaba node:sqlite:
# sin esto Python abre una transacción implícita en cada INSERT y no la cierra,
# y los datos quedan invisibles para cualquier otra conexión hasta el commit.
#
# `check_same_thread=False` + el lock: el servidor atiende cada request en su
# propio hilo y el pipeline de WhatsApp corre en hilos de cola, mientras que la
# versión anterior tenía un solo hilo para todo. El lock devuelve esa garantía:
# una operación por vez, en orden, como antes.
_conn = sqlite3.connect(config.db_path, check_same_thread=False, isolation_level=None)
_conn.row_factory = sqlite3.Row
_lock = threading.RLock()


@dataclass
class InfoEscritura:
    """Lo que devolvía `stmt.run()` de node:sqlite."""

    last_insert_rowid: int
    changes: int


def all(sql: str, *params: Any) -> list[dict[str, Any]]:
    """`stmt.all(...)`: todas las filas como dicts."""
    with _lock:
        return [dict(f) for f in _conn.execute(sql, params).fetchall()]


def get(sql: str, *params: Any) -> dict[str, Any] | None:
    """`stmt.get(...)`: la primera fila, o None."""
    with _lock:
        fila = _conn.execute(sql, params).fetchone()
        return dict(fila) if fila is not None else None


def run(sql: str, *params: Any) -> InfoEscritura:
    """`stmt.run(...)`: ejecuta y devuelve rowid y filas afectadas."""
    with _lock:
        cur = _conn.execute(sql, params)
        return InfoEscritura(last_insert_rowid=cur.lastrowid or 0, changes=cur.rowcount)


def exec_(sql: str) -> None:
    """`db.exec(...)`: una o varias sentencias sin parámetros.

    No usar dentro de `transaccion()`: `executescript` hace COMMIT antes de
    empezar y cortaría la transacción abierta. Sólo se usa para el esquema.
    """
    with _lock:
        _conn.executescript(sql)


_nivel_transaccion = 0


@contextmanager
def transaccion() -> Iterator[None]:
    """Agrupa varias sentencias en una operación indivisible.

    Node corría todo en un solo hilo: un SELECT y el UPDATE que lo sigue no
    podían intercalarse con nada, y el código de arriba dependía de eso sin
    decirlo. Acá el servidor atiende cada request en su hilo y encola por
    remitente, así que dos usuarios del MISMO productor escriben a la vez —
    y el lock por sentencia no alcanza: los dos leen el mismo stock y el
    último UPDATE pisa al otro.

    Este bloque sostiene el lock durante toda la secuencia (que es la garantía
    que daba el hilo único) y además la envuelve en una transacción de SQLite,
    para que un corte a mitad de camino no deje el evento sin su movimiento de
    stock.

    Reentrante: sólo el bloque más externo abre y cierra la transacción.
    """
    global _nivel_transaccion
    with _lock:
        externa = _nivel_transaccion == 0
        if externa:
            _conn.execute('BEGIN IMMEDIATE')
        _nivel_transaccion += 1
        try:
            yield
        except BaseException:
            _nivel_transaccion -= 1
            if externa:
                _conn.execute('ROLLBACK')
            raise
        else:
            _nivel_transaccion -= 1
            if externa:
                _conn.execute('COMMIT')


exec_('PRAGMA foreign_keys = ON;')

SCHEMA = """
CREATE TABLE IF NOT EXISTS productor (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre TEXT NOT NULL,
  pais TEXT NOT NULL,
  tipo_campo TEXT NOT NULL,                       -- agricola | ganadero | mixto
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS usuario (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  telefono TEXT NOT NULL UNIQUE,                  -- clave de match del webhook
  rol TEXT NOT NULL                               -- owner | gestor_campo
);
CREATE TABLE IF NOT EXISTS campo (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  hectareas REAL
);
CREATE TABLE IF NOT EXISTS lote (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  nombre TEXT NOT NULL,                           -- "Lote 4"
  numero TEXT,                                    -- "4"
  hectareas REAL,
  uso_actual TEXT
);
CREATE TABLE IF NOT EXISTS campania (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  cultivo_actividad TEXT,
  fecha_inicio TEXT,
  fecha_fin TEXT
);
CREATE TABLE IF NOT EXISTS movimiento (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  tipo TEXT NOT NULL,                             -- insumo | labor | gasto | venta
  lote_id INTEGER REFERENCES lote(id),
  campania_id INTEGER REFERENCES campania(id),
  fecha TEXT NOT NULL,
  producto TEXT,
  cantidad REAL,
  unidad TEXT,
  monto REAL,
  moneda TEXT DEFAULT 'ARS',
  categoria TEXT,
  descripcion TEXT,
  origen TEXT NOT NULL DEFAULT 'web',             -- bot | web | seed
  created_by INTEGER REFERENCES usuario(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS hacienda (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  categoria TEXT NOT NULL,                        -- ternero | vaca | novillo | toro | vaquillona
  cantidad INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evento_hacienda (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  tipo TEXT NOT NULL,                             -- nacimiento | muerte | compra | venta | traslado
  categoria TEXT NOT NULL,
  cantidad INTEGER NOT NULL,
  monto REAL,
  fecha TEXT NOT NULL,
  origen TEXT NOT NULL DEFAULT 'web',
  created_by INTEGER REFERENCES usuario(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evento_sanitario (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  producto TEXT,
  categoria TEXT,
  cantidad INTEGER,
  fecha TEXT NOT NULL,
  origen TEXT NOT NULL DEFAULT 'web',
  created_by INTEGER REFERENCES usuario(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS raw_message (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  usuario_id INTEGER REFERENCES usuario(id),
  texto TEXT NOT NULL,
  intent TEXT,
  record_type TEXT,
  parsed_json TEXT,
  confidence REAL,
  estado TEXT NOT NULL DEFAULT 'pending',         -- pending | confirmed | discarded
  wa_message_id TEXT,                             -- id de Meta (wamid.…); NULL si el envelope no lo trae
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  entidad TEXT NOT NULL,
  entidad_id INTEGER,
  accion TEXT NOT NULL,
  usuario_id INTEGER,
  origen TEXT,
  detalle TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _tiene_columna(tabla: str, col: str) -> bool:
    # PRAGMA no admite parámetros: `tabla` es siempre una constante del código.
    return any(c['name'] == col for c in all(f'PRAGMA table_info({tabla})'))


def _migrate() -> None:
    """Migraciones sobre bases ya creadas (el SCHEMA usa IF NOT EXISTS, así que una
    tabla existente no se actualiza sola). Idempotente: corre en cada arranque.
    """
    # SQLite NO permite `ADD COLUMN ... UNIQUE`: primero la columna, después el
    # índice. El índice único es lo que hace el dedup a prueba de carreras, y
    # admite infinitos NULL (los mensajes sin id de Meta conviven sin problema).
    if not _tiene_columna('raw_message', 'wa_message_id'):
        exec_('ALTER TABLE raw_message ADD COLUMN wa_message_id TEXT;')
    exec_('CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_message_wa_id ON raw_message(wa_message_id);')


def init_schema() -> None:
    exec_(SCHEMA)
    _migrate()


def is_empty() -> bool:
    init_schema()
    fila = get('SELECT COUNT(*) AS n FROM productor')
    return fila['n'] == 0


def drop_all() -> None:
    tablas = [
        'audit_log', 'raw_message', 'evento_sanitario', 'evento_hacienda',
        'hacienda', 'movimiento', 'campania', 'lote', 'campo', 'usuario', 'productor',
    ]
    for t in tablas:
        exec_(f'DROP TABLE IF EXISTS {t};')
    init_schema()
