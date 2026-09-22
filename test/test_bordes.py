"""Los bordes: entradas raras, fallas de infraestructura y salidas del modelo.

Nada de esto pasa en el camino feliz. Todo esto deja el sistema peor de lo
que parece cuando pasa: el server sin arrancar, la conexión envenenada, un
mensaje que se pierde sin rastro o un movimiento guardado en el lote
equivocado.
"""
import socket
import threading
import unittest

from backend.repository import db
from backend.service.parser import _normalizar_salida, parse_mock

from .utiles import base_limpia

NBSP = ' '


class Transacciones(unittest.TestCase):
    """La conexión tiene que sobrevivir a un COMMIT que falla.

    SQLite devuelve "database is locked" en el COMMIT si otra conexión está
    leyendo. Si eso deja la transacción abierta, el próximo `BEGIN` tira
    "cannot start a transaction within a transaction" y a partir de ahí no
    entra una escritura más ni carga el dashboard, hasta reiniciar.
    """

    def setUp(self) -> None:
        base_limpia()

    def test_se_recupera_de_una_transaccion_que_quedo_abierta(self) -> None:
        # El estado exacto que deja un COMMIT fallido.
        db._conn.execute('BEGIN IMMEDIATE')
        db._nivel_transaccion = 0

        with db.transaccion():
            db.run("INSERT INTO productor (nombre, pais, tipo_campo) VALUES ('X','AR','mixto')")
        self.assertEqual(len(db.all('SELECT id FROM productor')), 3)

        # Y el dashboard, que también abre transacción para su foto.
        from backend.service.dashboard import build_state
        self.assertIsNotNone(build_state(1))

    def test_el_contador_baja_aunque_el_bloque_falle(self) -> None:
        # Si el contador no volviera a cero, el próximo bloque se creería
        # anidado y no cerraría nunca la transacción.
        with self.assertRaises(RuntimeError):
            with db.transaccion():
                raise RuntimeError('falla simulada')
        self.assertEqual(db._nivel_transaccion, 0)
        self.assertFalse(db._conn.in_transaction)
        with db.transaccion():
            db.run("INSERT INTO productor (nombre, pais, tipo_campo) VALUES ('Y','AR','mixto')")


class ChunksDelWebhook(unittest.TestCase):
    """El tamaño de un chunk llega de afuera y se lee antes de verificar la firma."""

    @classmethod
    def setUpClass(cls) -> None:
        base_limpia()
        from backend.handler.server import _crear_servidor
        cls.servidor = _crear_servidor()
        cls.puerto = cls.servidor.server_address[1]
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def _enviar(self, tamano: bytes, cuerpo: bytes = b'hola') -> str:
        s = socket.create_connection(('127.0.0.1', self.puerto), timeout=5)
        try:
            s.sendall(b'POST /webhook/whatsapp HTTP/1.1\r\nHost: x\r\n'
                      b'Transfer-Encoding: chunked\r\nX-Hub-Signature-256: sha256=00\r\n\r\n'
                      + tamano + b'\r\n' + cuerpo + b'\r\n0\r\n\r\n')
            return s.recv(200).split(b'\r\n')[0].decode(errors='replace')
        except OSError:
            return '(conexión cortada)'
        finally:
            s.close()

    def test_rechaza_los_tamanos_mal_formados(self) -> None:
        # '-1' era el peligroso: pasaba el control de tamaño y terminaba en un
        # read(-1), o sea leer sin límite hasta EOF.
        for tamano in (b'-1', b'0x10', b'', b'zz', b'+4'):
            with self.subTest(tamano=tamano):
                self.assertIn('500', self._enviar(tamano))

    def test_acepta_los_tamanos_validos(self) -> None:
        # Llega a verificar la firma, que es falsa: 401, no 500.
        for tamano in (b'4', b' 4 ', b'0004'):
            with self.subTest(tamano=tamano):
                self.assertIn('401', self._enviar(tamano))

    def test_el_server_sigue_vivo_despues(self) -> None:
        self._enviar(b'-1')
        s = socket.create_connection(('127.0.0.1', self.puerto), timeout=5)
        try:
            s.sendall(b'GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n')
            self.assertIn('200', s.recv(200).split(b'\r\n')[0].decode())
        finally:
            s.close()


class LecturaDelEnv(unittest.TestCase):
    """Un .env cualquiera trae comentarios al final de la línea."""

    def _leer(self, texto: str) -> dict[str, str]:
        from backend.config import _parsear_env
        return _parsear_env(texto)

    def test_el_comentario_no_entra_en_el_valor(self) -> None:
        # Sin esto, PORT valía "3000 # local" y el server no arrancaba.
        self.assertEqual(self._leer('PORT=3000 # local')['PORT'], '3000')
        self.assertEqual(self._leer('PORT=3000#pegado')['PORT'], '3000')
        self.assertEqual(self._leer('VACIO=#todo')['VACIO'], '')

    def test_un_numeral_entre_comillas_es_parte_del_valor(self) -> None:
        # Un token o un secreto puede tener uno.
        self.assertEqual(self._leer('T="con # adentro"')['T'], 'con # adentro')
        self.assertEqual(self._leer("T='con # adentro'")['T'], 'con # adentro')
        self.assertEqual(self._leer('T="valor" # y comentario')['T'], 'valor')

    def test_un_valor_entrecomillado_puede_ocupar_varias_lineas(self) -> None:
        self.assertEqual(self._leer('K="una\ndos"\nOTRA=x'), {'K': 'una\ndos', 'OTRA': 'x'})

    def test_lo_demas_sigue_igual(self) -> None:
        self.assertEqual(
            self._leer('# comentario\n\nexport A=1\nB=  con espacios  \nC=\nA=2'),
            {'A': '2', 'B': 'con espacios', 'C': ''})   # una clave repetida: gana la última


class SalidaDelModelo(unittest.TestCase):
    """El modelo devuelve lo que quiere; el pipeline tiene que aguantarlo."""

    def test_los_numeros_que_llegan_como_texto_se_convierten(self) -> None:
        # Guardaba bien pero después reventaba al armar la respuesta, y el
        # mensaje ya estaba marcado como procesado: se perdía sin rastro.
        p = _normalizar_salida({'intent': 'create_record', 'recordType': 'insumo',
                                'fields': {'producto': 'gasoil', 'cantidad': '200', 'monto': '5000'},
                                'confidence': 0.9}, 'texto')
        self.assertEqual(p.fields['cantidad'], 200)
        self.assertEqual(p.fields['monto'], 5000)

    def test_lo_que_no_es_numero_se_trata_como_ausente(self) -> None:
        # Mejor que guardar un NaN, que deja el campo vacío igual pero sin que
        # nadie se entere: así salta la confirmación.
        p = _normalizar_salida({'fields': {'producto': 'urea', 'cantidad': 'muchos'}}, 'texto')
        self.assertNotIn('cantidad', p.fields)

    def test_no_toca_los_numeros_que_ya_vienen_bien(self) -> None:
        p = _normalizar_salida({'fields': {'cantidad': 8, 'monto': 2.5}}, 'texto')
        self.assertEqual((p.fields['cantidad'], p.fields['monto']), (8, 2.5))

    def test_aguanta_un_fields_que_no_es_un_objeto(self) -> None:
        self.assertEqual(_normalizar_salida({'fields': 'nada'}, 'texto').fields, {})


class EspaciosDeCelular(unittest.TestCase):
    """Los teclados de celular mandan espacios que no son el común.

    El no separable U+00A0 lo mete iOS solo. Con las regex en modo ASCII
    estricto, "lote{NBSP}4" no matcheaba: el movimiento se guardaba sin lote
    y sin pedir confirmación, así que nadie se enteraba.
    """

    def test_el_lote_se_reconoce_con_espacio_no_separable(self) -> None:
        normal = parse_mock('compré 200 litros de gasoil para el lote 4')
        raro = parse_mock(f'compré 200 litros de gasoil para el lote{NBSP}4')
        self.assertEqual(normal.fields['loteRef'], '4')
        self.assertEqual(raro.fields['loteRef'], '4')

    def test_tambien_en_los_montos_y_en_las_confirmaciones(self) -> None:
        self.assertEqual(parse_mock(f'pagué ${NBSP}600.000').fields['monto'], 600000)
        self.assertEqual(parse_mock(f'sí{NBSP}dale').intent, 'confirm')

    def test_los_bordes_de_palabra_siguen_siendo_ascii(self) -> None:
        # Lo que NO tiene que cambiar: `\b` sigue sin contar los acentos, o
        # "qué vendí" pasaría de registrar una venta a contestar cuánto vendí.
        self.assertEqual(parse_mock('que vendí').intent, 'query')
        self.assertEqual(parse_mock('qué vendí').record_type, 'venta')


if __name__ == '__main__':
    unittest.main()
