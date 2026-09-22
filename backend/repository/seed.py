"""Semilla de datos demo.

Crea dos productores que ejercitan las dos variantes:
 - "Estancia La Esperanza" (agrícola): lotes, movimientos y márgenes por lote.
 - "Don Pedro e Hijos" (ganadero): hacienda, eventos y sanidad.
"""
import sys

from ..jscompat import fecha_iso as hoy
from . import db
from .db import drop_all, init_schema, is_empty


def _ins_productor(nombre: str, pais: str, tipo: str) -> int:
    return db.run('INSERT INTO productor (nombre, pais, tipo_campo) VALUES (?,?,?)', nombre, pais, tipo).last_insert_rowid


def _ins_usuario(pid: int, nombre: str, tel: str, rol: str) -> int:
    return db.run('INSERT INTO usuario (productor_id, nombre, telefono, rol) VALUES (?,?,?,?)', pid, nombre, tel, rol).last_insert_rowid


def _ins_campo(pid: int, nombre: str, ha: float) -> int:
    return db.run('INSERT INTO campo (productor_id, nombre, hectareas) VALUES (?,?,?)', pid, nombre, ha).last_insert_rowid


def _ins_lote(pid: int, cid: int, nombre: str, numero: str, ha: float, uso: str) -> int:
    return db.run(
        'INSERT INTO lote (productor_id, campo_id, nombre, numero, hectareas, uso_actual) VALUES (?,?,?,?,?,?)',
        pid, cid, nombre, numero, ha, uso,
    ).last_insert_rowid


def _ins_campania(pid: int, nombre: str, act: str) -> int:
    return db.run(
        'INSERT INTO campania (productor_id, nombre, cultivo_actividad, fecha_inicio) VALUES (?,?,?,?)',
        pid, nombre, act, hoy(-120),
    ).last_insert_rowid


def _mov(pid: int, tipo: str, lote_id: int | None, fecha: str, producto: str | None,
         cantidad: float | None, unidad: str | None, monto: float, categoria: str | None, by: int) -> None:
    db.run(
        """INSERT INTO movimiento (productor_id, tipo, lote_id, fecha, producto, cantidad, unidad, monto, moneda, categoria, origen, created_by)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        pid, tipo, lote_id, fecha, producto, cantidad, unidad, monto, 'ARS', categoria, 'seed', by,
    )


def seed() -> None:
    init_schema()

    # ===== Productor 1: agrícola =====
    p1 = _ins_productor('Estancia La Esperanza', 'Argentina', 'agricola')
    u1o = _ins_usuario(p1, 'Juan Pérez', '+5491100000001', 'owner')
    u1g = _ins_usuario(p1, 'Juan Gómez', '+5491100000002', 'gestor_campo')
    c1 = _ins_campo(p1, 'Campo Norte', 320)
    l1 = _ins_lote(p1, c1, 'Lote 1', '1', 80, 'Soja')
    l4 = _ins_lote(p1, c1, 'Lote 4', '4', 100, 'Maíz')
    _ins_lote(p1, c1, 'Lote 7', '7', 60, 'Trigo')
    _ins_campania(p1, 'Gruesa 2025/26', 'Soja / Maíz')

    # Lote 1 (soja): costos + venta => margen confiable y positivo.
    _mov(p1, 'insumo', l1, hoy(-110), 'Semilla soja', 80, 'bolsa', 1_200_000, 'semilla', u1o)
    _mov(p1, 'insumo', l1, hoy(-108), 'Fertilizante', 4000, 'kg', 2_500_000, 'fertilizante', u1g)
    _mov(p1, 'labor', l1, hoy(-107), 'Siembra', None, None, 800_000, 'labor', u1g)
    _mov(p1, 'gasto', l1, hoy(-60), 'Fumigación', None, None, 600_000, 'servicios', u1g)
    _mov(p1, 'venta', l1, hoy(-10), 'Soja', 240, 'tn', 9_600_000, 'grano', u1o)

    # Lote 4 (maíz): solo costos, sin venta => margen NO confiable (lo demuestra la UI).
    _mov(p1, 'insumo', l4, hoy(-100), 'Semilla maíz', 1, 'bolsa', 1_500_000, 'semilla', u1o)
    _mov(p1, 'insumo', l4, hoy(-98), 'Urea', 5000, 'kg', 1_800_000, 'fertilizante', u1g)
    _mov(p1, 'labor', l4, hoy(-97), 'Siembra', None, None, 900_000, 'labor', u1g)

    # ===== Productor 2: ganadero =====
    p2 = _ins_productor('Don Pedro e Hijos', 'Argentina', 'ganadero')
    u2o = _ins_usuario(p2, 'Pedro Díaz', '+5491100000003', 'owner')
    u2g = _ins_usuario(p2, 'Marta Ruiz', '+5491100000004', 'gestor_campo')
    c2 = _ins_campo(p2, 'La Lomada', 500)

    # Stock inicial de hacienda.
    for cat, cant in (('vaca', 120), ('ternero', 45), ('novillo', 60), ('vaquillona', 30), ('toro', 4)):
        db.run('INSERT INTO hacienda (productor_id, campo_id, categoria, cantidad) VALUES (?,?,?,?)', p2, c2, cat, cant)

    # Eventos históricos (insertados directo, ya reflejados en el stock inicial).
    db.run(
        'INSERT INTO evento_hacienda (productor_id, campo_id, tipo, categoria, cantidad, fecha, origen, created_by) VALUES (?,?,?,?,?,?,?,?)',
        p2, c2, 'nacimiento', 'ternero', 12, hoy(-25), 'seed', u2g,
    )
    db.run(
        'INSERT INTO evento_hacienda (productor_id, campo_id, tipo, categoria, cantidad, monto, fecha, origen, created_by) VALUES (?,?,?,?,?,?,?,?,?)',
        p2, c2, 'venta', 'novillo', 15, 7_500_000, hoy(-15), 'seed', u2o,
    )

    # Movimientos económicos del ganadero.
    _mov(p2, 'gasto', None, hoy(-40), 'Ración', 8000, 'kg', 1_200_000, 'alimentacion', u2o)
    _mov(p2, 'venta', None, hoy(-15), 'Venta novillos', 15, 'cabeza', 7_500_000, 'hacienda', u2o)

    # Sanidad.
    db.run(
        'INSERT INTO evento_sanitario (productor_id, campo_id, producto, categoria, cantidad, fecha, origen, created_by) VALUES (?,?,?,?,?,?,?,?)',
        p2, c2, 'Vacuna aftosa', 'todos', 259, hoy(-30), 'seed', u2g,
    )

    print('✓ Semilla cargada: 2 productores (agrícola + ganadero).')


def seed_if_empty() -> None:
    if is_empty():
        seed()


# Ejecución directa: `python3 -m backend.repository.seed` (o `--reset` para borrar y recargar).
if __name__ == '__main__':
    if '--reset' in sys.argv:
        drop_all()
        print('✓ Base reiniciada.')
    seed()
