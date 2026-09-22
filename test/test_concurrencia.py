"""Regresión: escrituras concurrentes sobre el mismo productor.

El servidor atiende cada request en su hilo y encola por remitente, así que
dos usuarios del MISMO productor (el dueño y el gestor de campo, que es justo
lo que trae la semilla) pueden estar escribiendo a la vez.

`adjust_hacienda` lee el stock, le suma el delta y lo guarda: son tres
sentencias. Sin una transacción alrededor los dos hilos leen el mismo stock y
el último UPDATE se come el delta del otro — quedan los eventos registrados y
el stock corto, y nada avisa.
"""
import threading
import unittest

# Este módulo tira TODAS las tablas en cada `setUp`. Si se ejecuta como archivo
# suelto, `test/__init__.py` no corre, `DB_PATH` no se fija y el borrado caería
# sobre data/parva.db. La verificación va antes de importar nada del backend,
# y con un mensaje que diga qué hacer en vez de un ImportError de relativos.
if __package__ in (None, ''):
    raise SystemExit(
        'Este test borra todas las tablas y necesita la base temporal que arma\n'
        'test/__init__.py. Correlo como paquete:\n'
        '  python3 -m unittest discover -s test -t .\n'
        '  python3 -m unittest test.test_concurrencia')

from backend.repository import db, repo

# Importar desde `.utiles` no es decorativo: trae la guarda que comprueba que
# la base sea la temporal. Este módulo tira todas las tablas en cada `setUp`,
# así que corriéndolo fuera del paquete se llevaría puesta data/parva.db.
from .utiles import base_vacia

HILOS = 8
EVENTOS_POR_HILO = 40


class StockConcurrente(unittest.TestCase):
    def setUp(self) -> None:
        base_vacia()
        self.pid = db.run(
            "INSERT INTO productor (nombre, pais, tipo_campo) VALUES ('Test','Argentina','ganadero')"
        ).last_insert_rowid
        self.cid = db.run(
            'INSERT INTO campo (productor_id, nombre, hectareas) VALUES (?,?,?)', self.pid, 'Campo', 100
        ).last_insert_rowid

    def _en_paralelo(self, trabajo) -> None:
        """Arranca los hilos a la vez, para que peleen de verdad por el stock."""
        largada = threading.Barrier(HILOS)
        fallos: list[BaseException] = []

        def correr(n: int) -> None:
            largada.wait()
            try:
                trabajo(n)
            except BaseException as e:      # pragma: no cover
                fallos.append(e)

        hilos = [threading.Thread(target=correr, args=(i,)) for i in range(HILOS)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(fallos, [], 'algún hilo falló')

    def test_los_nacimientos_concurrentes_no_pierden_stock(self) -> None:
        db.run('INSERT INTO hacienda (productor_id, campo_id, categoria, cantidad) VALUES (?,?,?,0)',
               self.pid, self.cid, 'ternero')

        def cargar(_: int) -> None:
            for _ in range(EVENTOS_POR_HILO):
                repo.insert_evento_hacienda(
                    productor_id=self.pid, campo_id=self.cid, tipo='nacimiento',
                    categoria='ternero', cantidad=1, fecha='2026-09-22', origen='bot')

        self._en_paralelo(cargar)

        esperado = HILOS * EVENTOS_POR_HILO
        eventos = db.get('SELECT COUNT(*) AS n FROM evento_hacienda WHERE productor_id = ?', self.pid)
        stock = db.get('SELECT cantidad FROM hacienda WHERE productor_id = ? AND categoria = ?',
                       self.pid, 'ternero')
        self.assertEqual(eventos['n'], esperado)
        self.assertEqual(stock['cantidad'], esperado,
                         'el stock no coincide con los eventos: se perdieron updates')

    def test_la_fila_de_stock_se_crea_una_sola_vez(self) -> None:
        """Sin transacción, dos hilos pueden no encontrar la fila y crearla los dos."""
        def cargar(_: int) -> None:
            for _ in range(EVENTOS_POR_HILO):
                repo.insert_evento_hacienda(
                    productor_id=self.pid, campo_id=self.cid, tipo='compra',
                    categoria='novillo', cantidad=1, fecha='2026-09-22', origen='bot')

        self._en_paralelo(cargar)

        filas = db.all('SELECT cantidad FROM hacienda WHERE productor_id = ? AND categoria = ?',
                       self.pid, 'novillo')
        self.assertEqual(len(filas), 1, 'se creó más de una fila de stock para la misma categoría')
        self.assertEqual(filas[0]['cantidad'], HILOS * EVENTOS_POR_HILO)

    def test_cada_movimiento_queda_con_su_auditoria(self) -> None:
        def cargar(_: int) -> None:
            for _ in range(EVENTOS_POR_HILO):
                repo.insert_movimiento(productor_id=self.pid, tipo='gasto', fecha='2026-09-22',
                                       monto=1000, origen='bot')

        self._en_paralelo(cargar)

        esperado = HILOS * EVENTOS_POR_HILO
        movs = db.get('SELECT COUNT(*) AS n FROM movimiento WHERE productor_id = ?', self.pid)
        audit = db.get("SELECT COUNT(*) AS n FROM audit_log WHERE productor_id = ? AND entidad = 'movimiento'",
                       self.pid)
        self.assertEqual(movs['n'], esperado)
        self.assertEqual(audit['n'], esperado)


class Atomicidad(unittest.TestCase):
    """El evento de hacienda y el stock que mueve son un solo hecho.

    Si el INSERT entra y el ajuste de stock falla, queda un nacimiento
    registrado que no se ve en el stock: los números dejan de cerrar y no hay
    forma de darse cuenta salvo sumando los eventos a mano.
    """

    def setUp(self) -> None:
        base_vacia()
        self.pid = db.run(
            "INSERT INTO productor (nombre, pais, tipo_campo) VALUES ('Test','Argentina','ganadero')"
        ).last_insert_rowid

    def test_si_falla_el_ajuste_de_stock_no_queda_el_evento(self) -> None:
        def explotar(*_args, **_kwargs):
            raise RuntimeError('falla simulada al mover el stock')

        original = repo.adjust_hacienda
        repo.adjust_hacienda = explotar
        try:
            with self.assertRaises(RuntimeError):
                repo.insert_evento_hacienda(
                    productor_id=self.pid, tipo='nacimiento', categoria='ternero',
                    cantidad=5, fecha='2026-09-22', origen='bot')
        finally:
            repo.adjust_hacienda = original

        self.assertEqual(db.all('SELECT id FROM evento_hacienda'), [],
                         'el evento tendría que haberse deshecho junto con el stock')
        self.assertEqual(db.all("SELECT id FROM audit_log WHERE entidad = 'evento_hacienda'"), [])

    def test_si_falla_la_auditoria_no_queda_el_movimiento(self) -> None:
        original = repo.audit
        repo.audit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('falla simulada'))
        try:
            with self.assertRaises(RuntimeError):
                repo.insert_movimiento(productor_id=self.pid, tipo='gasto',
                                       fecha='2026-09-22', monto=1000, origen='bot')
        finally:
            repo.audit = original
        self.assertEqual(db.all('SELECT id FROM movimiento'), [],
                         'un movimiento sin su auditoría no tendría que quedar')


if __name__ == '__main__':
    unittest.main()
