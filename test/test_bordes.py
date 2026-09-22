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

    def _con_content_length(self, valor: bytes) -> str:
        s = socket.create_connection(('127.0.0.1', self.puerto), timeout=5)
        try:
            s.sendall(b'POST /webhook/whatsapp HTTP/1.1\r\nHost: x\r\nContent-Length: '
                      + valor + b'\r\nX-Hub-Signature-256: sha256=00\r\n\r\nhola')
            return s.recv(200).split(b'\r\n')[0].decode(errors='replace')
        except OSError:
            return '(conexión cortada)'
        finally:
            s.close()

    def test_rechaza_un_content_length_mal_formado(self) -> None:
        # Un valor no numérico tiraba un traceback entero por pedido, y el
        # webhook está expuesto a internet.
        for valor in (b'abc', b'-5', b'99999999999999999999', b'4,5'):
            with self.subTest(valor=valor):
                self.assertIn('500', self._con_content_length(valor))

    def test_acepta_un_content_length_normal(self) -> None:
        # Llega a verificar la firma, que es falsa: 401, no 500.
        self.assertIn('401', self._con_content_length(b'4'))

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


class SalidaDelModeloConTiposRaros(unittest.TestCase):
    """El modelo devuelve JSON libre y nadie chequea tipos río abajo.

    `loteRef` va a parar a un `.strip()`, `unidad` se concatena a un string y
    `descripcion` se bindea a SQLite. Cada uno revienta con un error distinto,
    y siempre tarde: con el movimiento ya guardado y el mensaje marcado como
    procesado, así que el productor no recibe nada y el reintento de Meta se
    descarta por duplicado.
    """

    def setUp(self) -> None:
        base_limpia()
        from backend.repository.repo import get_sender_by_telefono
        self.sender = get_sender_by_telefono('+5491100000001')

    def _procesar(self, crudo: dict, marca: str):
        import backend.service.process as proc
        parsed = _normalizar_salida(crudo, 'mensaje del productor')
        original = proc.parse
        proc.parse = lambda _t: parsed
        try:
            return proc.process_message(self.sender, 'mensaje del productor', f'wamid.raro.{marca}')
        finally:
            proc.parse = original

    def test_un_numero_donde_va_texto_se_convierte(self) -> None:
        # `loteRef: 4` claramente quiso decir "lote 4": se aprovecha.
        r = self._procesar({'intent': 'create_record', 'recordType': 'insumo',
                            'fields': {'producto': 'gasoil', 'cantidad': 5, 'loteRef': 4},
                            'confidence': 0.9}, 'lote')
        self.assertIn('en el lote 4', r.reply)

    def test_una_unidad_numerica_no_rompe_el_mensaje(self) -> None:
        r = self._procesar({'intent': 'create_record', 'recordType': 'insumo',
                            'fields': {'producto': 'gasoil', 'cantidad': 5, 'unidad': 7},
                            'confidence': 0.9}, 'unidad')
        self.assertTrue(r.reply.startswith('✅'))

    def test_una_lista_donde_va_texto_se_descarta(self) -> None:
        # Bindear una lista a SQLite tira ProgrammingError con la fila a medias.
        r = self._procesar({'intent': 'create_record', 'recordType': 'gasto',
                            'fields': {'monto': 100, 'descripcion': ['a', 'b']},
                            'confidence': 0.9}, 'desc')
        self.assertEqual(r.status, 'created')
        self.assertIsNone(db.get("SELECT descripcion FROM movimiento WHERE origen='bot'")['descripcion'])

    def test_una_query_que_no_es_objeto_no_rompe(self) -> None:
        r = self._procesar({'intent': 'query', 'query': ['margen'], 'confidence': 0.9}, 'query')
        self.assertEqual(r.status, 'unknown')

    def test_una_metrica_inventada_no_se_reporta_como_permisos(self) -> None:
        # Decir "tu rol no puede consultar lo_que_sea" manda a buscar un
        # problema de permisos que no existe.
        r = self._procesar({'intent': 'query', 'query': {'metric': 'lo_que_sea'},
                            'confidence': 0.9}, 'metric')
        self.assertEqual(r.reply, 'No pude responder esa consulta.')
        self.assertNotIn('rol', r.reply)

    def test_un_tipo_de_evento_inventado_se_pide_de_nuevo(self) -> None:
        # Un evento con un tipo desconocido no mueve stock: sería una fila que
        # no significa nada.
        r = self._procesar({'intent': 'create_record', 'recordType': 'evento_hacienda',
                            'fields': {'categoriaAnimal': 'vaca', 'eventoTipo': 'teletransporte',
                                       'cantidad': 3}, 'confidence': 0.9}, 'evento')
        self.assertEqual(r.status, 'needs_data')
        self.assertEqual(db.all("SELECT id FROM evento_hacienda WHERE origen='bot'"), [])


class MensajesQueNoMienten(unittest.TestCase):
    """El recibo tiene que describir lo que efectivamente pasó."""

    def setUp(self) -> None:
        base_limpia()
        from backend.repository.repo import get_sender_by_telefono
        from backend.service.process import process_message
        self.sender = get_sender_by_telefono('+5491100000003')   # productor ganadero
        self.procesar = process_message

    def test_un_traslado_no_dice_que_actualizo_el_stock(self) -> None:
        # Un traslado cambia de campo, no el total: el stock queda igual.
        antes = db.get("SELECT cantidad FROM hacienda WHERE productor_id=2 AND categoria='novillo'")
        r = self.procesar(self.sender, 'trasladé 20 novillos', 'wamid.tras.1')
        despues = db.get("SELECT cantidad FROM hacienda WHERE productor_id=2 AND categoria='novillo'")
        self.assertEqual(antes['cantidad'], despues['cantidad'])
        self.assertNotIn('Stock actualizado', r.reply)
        self.assertIn('El stock total no cambia', r.reply)

    def test_un_nacimiento_si_lo_dice(self) -> None:
        r = self.procesar(self.sender, 'nacieron 8 terneros', 'wamid.tras.2')
        self.assertIn('Stock actualizado', r.reply)


class PendienteIlegible(unittest.TestCase):
    """Un `parsed_json` que no se puede leer no puede trabar el "sí"."""

    def setUp(self) -> None:
        base_limpia()
        from backend.repository.repo import get_sender_by_telefono
        self.sender = get_sender_by_telefono('+5491100000001')

    def _pendiente(self, payload: str) -> None:
        db.run("INSERT INTO raw_message (productor_id, usuario_id, texto, parsed_json, estado) "
               "VALUES (?,?,?,?,'pending')", self.sender.productor_id, self.sender.usuario_id,
               'pendiente viejo', payload)

    def test_un_json_roto_cierra_el_pendiente_igual(self) -> None:
        # Parsearlo falla ANTES de intentar guardarlo, así que el `try` tiene
        # que abarcar la lectura y no sólo el persist.
        from backend.service.process import process_message
        for payload in ('{no soy json', '[1,2,3]', '42', '{"loteId":null}'):
            with self.subTest(payload=payload):
                db.run("DELETE FROM raw_message")
                self._pendiente(payload)
                with self.assertRaises(Exception):
                    process_message(self.sender, 'sí', f'wamid.ileg.{payload[:6]}')
                self.assertEqual(db.all("SELECT id FROM raw_message WHERE estado='pending'"), [],
                                 'el pendiente tendría que quedar cerrado')


class VariablesDeEntorno(unittest.TestCase):
    """Una variable mal escrita no puede apagar un guardrail en silencio."""

    def _leer(self, valor, por_defecto):
        import os

        from backend.config import _numero_o_default
        previo = os.environ.get('UNA_PRUEBA')
        if valor is None:
            os.environ.pop('UNA_PRUEBA', None)
        else:
            os.environ['UNA_PRUEBA'] = valor
        try:
            return _numero_o_default('UNA_PRUEBA', por_defecto)
        finally:
            if previo is None:
                os.environ.pop('UNA_PRUEBA', None)
            else:
                os.environ['UNA_PRUEBA'] = previo

    def test_un_valor_ilegible_cae_al_default(self) -> None:
        # Con NaN, `confianza < umbral` es siempre False: se guardaba todo sin
        # pedir confirmación y no había ni un error que lo delatara.
        self.assertEqual(self._leer('abc', 0.7), 0.7)
        self.assertEqual(self._leer('', 0.7), 0.7)
        self.assertEqual(self._leer('   ', 0.7), 0.7)
        self.assertEqual(self._leer(None, 0.7), 0.7)

    def test_un_valor_bueno_se_respeta(self) -> None:
        self.assertEqual(self._leer('0.5', 0.7), 0.5)
        self.assertEqual(self._leer('0', 0.7), 0)


class TextoLargo(unittest.TestCase):
    """Un mensaje largo no puede colgar la cola del remitente."""

    def test_el_parser_es_lineal_con_los_digitos(self) -> None:
        """Duplicar la entrada tiene que duplicar el tiempo, no cuadruplicarlo.

        `[\\d.,]+` seguido de "pesos" probaba desde cada posición consumiendo
        todos los dígitos: 100.000 dígitos tardaban dos minutos con el hilo de
        esa cola bloqueado. Se mide la curva y no el tiempo absoluto, para que
        el test falle en milisegundos en vez de tardar lo que tarda el bug.
        """
        import time

        def medir(n: int) -> float:
            t0 = time.perf_counter()
            parse_mock('compré ' + '1' * n + ' litros')
            return time.perf_counter() - t0

        medir(2000)                       # calentar, para no medir el import
        chico, grande = medir(4000), medir(8000)
        # Lineal daría ~2; cuadrático, ~4. El margen es por el ruido de medir.
        self.assertLess(grande / max(chico, 1e-6), 3.0,
                        f'el parser se volvió cuadrático: {chico*1000:.1f} ms → {grande*1000:.1f} ms')

    def test_sigue_leyendo_bien_un_importe_normal(self) -> None:
        self.assertEqual(parse_mock('pagué 1.200.000 pesos').fields['monto'], 1200000)
        self.assertEqual(parse_mock('pagué 500 pesos').fields['monto'], 500)


if __name__ == '__main__':
    unittest.main()
