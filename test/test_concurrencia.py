"""Regresión: escrituras concurrentes sobre el mismo productor.

Node corría todo en un solo hilo, así que el leer-modificar-escribir de
`adjust_hacienda` era indivisible sin que nadie tuviera que pedirlo. El
servidor Python atiende cada request en su hilo y encola por remitente, así
que dos usuarios del MISMO productor (el owner y el gestor de campo, que es
justo lo que trae la semilla) pueden estar escribiendo a la vez.

Sin una transacción alrededor, los dos leen el mismo stock y el último UPDATE
se come el delta del otro: quedan los eventos registrados y el stock corto.
"""
import os
import tempfile
import threading
import unittest

# DB_PATH tiene que estar antes de importar el backend: la conexión se abre al
# importar el módulo, igual que hacía la versión anterior.
os.environ['DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='parva-test-'), 'parva.db')

from backend.repository import db, repo  # noqa: E402

HILOS = 8
EVENTOS_POR_HILO = 40


class StockConcurrente(unittest.TestCase):
    def setUp(self) -> None:
        db.drop_all()
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


if __name__ == '__main__':
    unittest.main()
