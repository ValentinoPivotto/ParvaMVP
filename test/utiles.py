"""Helpers de los tests: golden files, fechas y base limpia.

La mayoría de los tests son de caracterización: corren una pieza y comparan la
salida contra un archivo en `test/golden/`. Esos archivos son el contrato: el
texto exacto que el bot contesta, los bytes exactos del CSV que se baja el
productor, las respuestas HTTP con sus headers. Nada de eso se puede cambiar
sin que alguien lo note, así que tampoco se cambia sin querer.

Para regenerarlos después de un cambio *intencional*:

    ACTUALIZAR_GOLDEN=1 python3 -m unittest discover -s test -t .

Ojo con eso: regenerar a ciegas convierte cualquier regresión en el nuevo
esperado. Hay que mirar el `git diff` del golden antes de commitearlo.
"""
import difflib
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import DIR_TEMPORAL

RAIZ = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / 'golden'
CORPUS = Path(__file__).resolve().parent / 'corpus.json'

def exigir_base_temporal() -> None:
    """Aborta si la base no es la temporal de los tests.

    Se chequea al importar y otra vez **en el momento de borrar**. Lo segundo
    no es paranoia: `base_vacia()` tira todas las tablas, y si alguien llega
    hasta ahí con la base de verdad configurada, se lleva puestos los datos
    del productor.
    """
    if not os.environ.get('DB_PATH', '').startswith(DIR_TEMPORAL):
        raise RuntimeError(
            'El entorno de test no está configurado y la base apunta a '
            f'{os.environ.get("DB_PATH", "(sin DB_PATH)")!r}.\n'
            'Corré los tests como paquete:  python3 -m unittest discover -s test -t .')


exigir_base_temporal()

_FECHA = re.compile(r'\d{4}-\d{2}-\d{2}')
# La hora de `created_at`, que sale de datetime('now') y cambia en cada corrida.
_HORA = re.compile(r'(\{HOY[^}]*\}) \d{2}:\d{2}:\d{2}')
_DESDE, _HASTA = -400, 2


def _mapa_fechas() -> dict[str, str]:
    """Fecha real → token, para los días que el código puede llegar a producir.

    Se replica la semántica de `formato.fecha_iso`: el día se mueve en hora
    local y recién después se pasa a UTC.
    """
    ahora = datetime.now()
    mapa: dict[str, str] = {}
    for offset in range(_DESDE, _HASTA):
        fecha = (ahora + timedelta(days=offset)).astimezone(timezone.utc).date().isoformat()
        mapa.setdefault(fecha, '{HOY}' if offset == 0 else '{HOY%+d}' % offset)
    return mapa


def normalizar_fechas(texto: str) -> str:
    """Cambia fechas y horas por tokens, para que el golden no caduque.

    Sin esto los golden vencerían cada medianoche: la semilla fecha sus
    movimientos con offsets contra el día de hoy y el parser resuelve "ayer".
    La hora de `created_at` también se neutraliza: sale de `datetime('now')` y
    cambia en cada corrida.
    """
    mapa = _mapa_fechas()
    texto = _FECHA.sub(lambda m: mapa.get(m.group(0), m.group(0)), texto)
    return _HORA.sub(r'\1 {HORA}', texto)


def base_vacia() -> None:
    """Tira todas las tablas y vuelve a crear el esquema, sin datos."""
    exigir_base_temporal()
    from backend.repository.db import drop_all
    drop_all()


def base_limpia() -> None:
    """Borra y vuelve a sembrar. Deja la base como en el primer arranque.

    El "✓ Semilla cargada" de `seed()` se traga: en un `setUp` sale una vez por
    test y tapa la salida del runner.
    """
    import contextlib
    import io

    from backend.repository.seed import seed
    base_vacia()
    with contextlib.redirect_stdout(io.StringIO()):
        seed()


def volcar_tablas(tablas: list[str]) -> str:
    """Dump legible de las tablas indicadas, sin las columnas de tiempo.

    `created_at` sale de `datetime('now')` y cambia en cada corrida, así que no
    puede formar parte del golden.
    """
    from backend.repository import db
    partes: list[str] = []
    for tabla in tablas:
        partes.append(f'=== {tabla} ===')
        for fila in db.all(f'SELECT * FROM {tabla} ORDER BY id'):
            campos = {k: v for k, v in fila.items() if k != 'created_at'}
            partes.append(' | '.join(f'{k}={v!r}' for k, v in campos.items()))
    return '\n'.join(partes) + '\n'


def comparar_golden(caso: unittest.TestCase, nombre: str, producido: str) -> None:
    """Compara contra `test/golden/<nombre>`, o lo reescribe si ACTUALIZAR_GOLDEN=1."""
    ruta = GOLDEN / nombre
    producido = normalizar_fechas(producido)

    if os.environ.get('ACTUALIZAR_GOLDEN') == '1':
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(producido, encoding='utf-8')
        return

    if not ruta.exists():
        caso.fail(f'Falta el golden {ruta.relative_to(RAIZ)}. '
                  f'Generalo con ACTUALIZAR_GOLDEN=1 y revisá el diff antes de commitear.')

    esperado = ruta.read_text(encoding='utf-8')
    if esperado == producido:
        return

    diff = '\n'.join(difflib.unified_diff(
        esperado.splitlines(), producido.splitlines(),
        fromfile=f'golden/{nombre} (esperado)', tofile='salida actual', lineterm=''))
    # Un golden grande no entra en pantalla: se recorta y se dice cuánto falta.
    lineas = diff.splitlines()
    if len(lineas) > 60:
        diff = '\n'.join(lineas[:60]) + f'\n… ({len(lineas) - 60} líneas más de diferencia)'
    caso.fail(f'La salida cambió respecto de golden/{nombre}:\n{diff}')
