"""Los bordes: entradas raras, fallas de infraestructura y salidas del modelo.

Nada de esto pasa en el camino feliz. Todo esto deja el sistema peor de lo
que parece cuando pasa: el server sin arrancar, la conexión envenenada, un
mensaje que se pierde sin rastro o un movimiento guardado en el lote
equivocado.
"""
import http.client
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

    def _respuesta(self, pedido: bytes) -> str:
        """El status y el cuerpo de la respuesta a un pedido crudo."""
        s = socket.create_connection(('127.0.0.1', self.puerto), timeout=5)
        try:
            s.sendall(pedido)
            res = http.client.HTTPResponse(s)
            res.begin()
            return f'{res.status} {res.read().decode(errors="replace")}'
        except (OSError, http.client.HTTPException):
            return '(conexión cortada)'
        finally:
            s.close()

    def _con_content_length(self, valor: bytes) -> str:
        return self._respuesta(b'POST /webhook/whatsapp HTTP/1.1\r\nHost: x\r\nContent-Length: '
                               + valor + b'\r\nX-Hub-Signature-256: sha256=00\r\n\r\nhola')

    def test_rechaza_un_content_length_mal_formado(self) -> None:
        # Un valor no numérico tiraba un traceback entero por pedido, y el
        # webhook está expuesto a internet.
        for valor in (b'abc', b'-5', b'99999999999999999999', b'4,5'):
            with self.subTest(valor=valor):
                self.assertIn('500', self._con_content_length(valor))

    def test_un_content_length_con_digitos_que_no_son_ascii(self) -> None:
        # '²' es un dígito para `isdigit()`, pero `int()` no lo convierte.
        self.assertEqual(self._con_content_length(b'\xb2'), '500 {"error":"Content-Length inválido"}')

    def test_un_content_length_de_miles_de_cifras(self) -> None:
        # `int()` no convierte más de 4300 cifras.
        self.assertEqual(self._con_content_length(b'9' * 5000), '500 {"error":"body demasiado grande"}')

    def test_un_content_length_con_ceros_adelante(self) -> None:
        # 0…04 es 4, por más cifras que tenga.
        self.assertEqual(self._con_content_length(b'0' * 5000 + b'4'), '401 firma inválida')

    def test_una_linea_de_chunk_sin_fin(self) -> None:
        # Sin límite, el server se queda leyendo (y guardando) hasta que
        # llegue un fin de línea que nunca llega.
        pedido = (b'POST /webhook/whatsapp HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n'
                  b'X-Hub-Signature-256: sha256=00\r\n\r\n4;' + b'x' * 65535)
        self.assertEqual(self._respuesta(pedido), '500 {"error":"línea de chunk demasiado larga"}')

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


class EntornoDeLosTests(unittest.TestCase):
    def test_fija_todas_las_variables_del_env(self) -> None:
        """Una variable que el entorno de los tests no fija la toma del .env de
        quien los corre: con credenciales de AWS ahí, los tests saldrían a
        Bedrock. Una variable nueva en .env.example va también en
        test/__init__.py."""
        import re

        from . import ENTORNO
        from .utiles import RAIZ
        documentadas = re.findall(r'^#?\s*([A-Z][A-Z0-9_]+)=',
                                  (RAIZ / '.env.example').read_text(encoding='utf-8'), re.M)
        self.assertTrue(documentadas)
        self.assertEqual(sorted(set(documentadas) - ENTORNO.keys()), [])


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


class ImportesQueNoSonNumeros(unittest.TestCase):
    """Un número demasiado grande para ser un número.

    400 dígitos entran en un mensaje de WhatsApp y `float` los convierte en
    infinito. Guardado, envenenaba la base: cada consulta que lo sumara
    explotaba al formatearlo, para siempre, y ese productor se quedaba sin
    poder preguntar cuánto gastó.
    """

    def setUp(self) -> None:
        base_limpia()
        from backend.repository.repo import get_sender_by_telefono
        from backend.service.process import process_message
        self.sender = get_sender_by_telefono('+5491100000001')
        self.procesar = process_message

    def test_el_parser_lo_trata_como_si_no_hubiera_monto(self) -> None:
        self.assertIsNone(parse_mock('pagué $' + '9' * 400).fields.get('monto'))
        self.assertIsNone(parse_mock('compré ' + '9' * 400 + ' litros de gasoil').fields.get('cantidad'))

    def test_no_llega_a_guardarse(self) -> None:
        r = self.procesar(self.sender, 'pagué $' + '9' * 400, 'wamid.inf.1')
        self.assertEqual(r.status, 'needs_confirmation')     # "no entendí el monto del gasto"
        self.assertEqual(db.all("SELECT id FROM movimiento WHERE origen='bot'"), [])

    def test_una_base_ya_envenenada_sigue_contestando(self) -> None:
        # Por si quedó guardado antes del arreglo: la consulta no puede morir.
        db.run("INSERT INTO movimiento (productor_id, tipo, fecha, monto, origen) VALUES (1,'gasto','2026-01-01',?,'bot')",
               float('inf'))
        r = self.procesar(self.sender, '¿cuánto gasté?', 'wamid.inf.2')
        self.assertEqual(r.reply, 'Gasto total registrado: $∞.')

    def test_el_formato_de_pesos_nunca_tira(self) -> None:
        from backend.formato import pesos
        self.assertEqual(pesos(float('inf')), '$∞')
        self.assertEqual(pesos(float('-inf')), '$-∞')
        self.assertEqual(pesos(float('nan')), '$—')

    def test_la_salida_del_modelo_tampoco_trae_infinitos(self) -> None:
        p = _normalizar_salida({'fields': {'monto': '1e999', 'cantidad': 10 ** 30},
                                'confidence': float('nan')}, 'texto')
        self.assertNotIn('monto', p.fields)
        # Un entero gigante pasa a float: SQLite no acepta enteros de más de 64 bits.
        self.assertEqual(p.fields['cantidad'], 1e30)
        # Una confianza NaN hacía que `confianza < umbral` diera siempre False.
        self.assertEqual(p.confidence, 0.8)


class EspaciosQueTraeElCopyPaste(unittest.TestCase):
    """El BOM (U+FEFF) al principio de un mensaje: lo arrastra algún copy-paste.

    `str.strip()` no lo saca, así que un "sí" pegado con un BOM adelante no
    confirmaba el pendiente.
    """

    def test_un_si_con_bom_confirma(self) -> None:
        self.assertEqual(parse_mock('\ufeffsí').intent, 'confirm')
        self.assertEqual(parse_mock('\ufeffcompré 200 litros de gasoil').record_type, 'insumo')

    def test_el_recorte_es_el_de_los_espacios_que_se_escriben(self) -> None:
        from backend.formato import recortar
        self.assertEqual(recortar(f'\ufeff {NBSP}sí\u3000 '), 'sí')
        # Los separadores de control no son espacios que escriba nadie.
        self.assertEqual(recortar('\x1csí'), '\x1csí')

    def test_un_mensaje_que_es_solo_un_bom_se_ignora(self) -> None:
        from backend.handler import server
        from backend.handler.whatsapp import MensajeEntrante
        base_limpia()
        llamados: list[str] = []
        proceso, envio = server.process_message, server.enviar_texto
        server.process_message = lambda *a, **k: llamados.append('procesado')
        server.enviar_texto = lambda *a, **k: llamados.append('enviado')
        try:
            server.manejar_entrante(MensajeEntrante(
                wa_message_id='wamid.bom', from_='5491100000001', phone_number_id='N',
                waba_id='W', tipo='text', texto='\ufeff', timestamp=''))
        finally:
            server.process_message, server.enviar_texto = proceso, envio
        self.assertEqual(llamados, [])


class LoteEnBlanco(unittest.TestCase):
    """Un `loteRef` de puros espacios no es "el primer lote"."""

    def setUp(self) -> None:
        base_limpia()

    def test_el_saneado_descarta_los_textos_en_blanco(self) -> None:
        # Hay dos defensas: ésta y la guarda de `find_lote_by_ref`. Cada una
        # tiene su test, así que sacar cualquiera de las dos se nota.
        p = _normalizar_salida({'fields': {'loteRef': '   ', 'producto': f' gasoil{NBSP}', 'unidad': ''}}, 'x')
        self.assertEqual(p.fields, {'producto': 'gasoil'})

    def test_no_se_resuelve_a_ningun_lote(self) -> None:
        from backend.repository.repo import find_lote_by_ref
        self.assertIsNone(find_lote_by_ref(1, '   '))
        self.assertIsNone(find_lote_by_ref(1, ''))
        self.assertEqual(find_lote_by_ref(1, '4')['nombre'], 'Lote 4')

    def test_el_modelo_no_puede_cargarlo_al_primer_lote(self) -> None:
        import backend.service.process as proc
        from backend.repository.repo import get_sender_by_telefono
        parsed = _normalizar_salida({'intent': 'create_record', 'recordType': 'insumo', 'confidence': 0.9,
                                     'fields': {'producto': 'gasoil', 'cantidad': 5, 'loteRef': '  '}},
                                    'mensaje')
        original = proc.parse
        proc.parse = lambda _t: parsed
        try:
            r = proc.process_message(get_sender_by_telefono('+5491100000001'), 'mensaje', 'wamid.blanco')
        finally:
            proc.parse = original
        self.assertEqual(r.reply, '✅ Registré 5 de gasoil.')
        self.assertIsNone(db.get("SELECT lote_id FROM movimiento WHERE origen='bot'")['lote_id'])

    def test_una_consulta_con_lote_numerico_se_contesta(self) -> None:
        # La `query` no se saneaba: `loteRef: 1` terminaba en un `.strip()`.
        from backend.repository.repo import get_sender_by_telefono
        from backend.service.query import answer_query
        q = _normalizar_salida({'intent': 'query', 'query': {'metric': 'margen', 'loteRef': 1}}, 'x').query
        s = get_sender_by_telefono('+5491100000001')
        self.assertIn('Margen del Lote 1', answer_query(s.productor_id, s.rol, q).text)


class EnvConBom(unittest.TestCase):
    """Un .env guardado con BOM (el Bloc de notas de Windows lo hace)."""

    def test_la_primera_variable_no_se_pierde(self) -> None:
        import os
        import pathlib
        import tempfile

        from backend.config import _cargar_env
        ruta = pathlib.Path(tempfile.mkdtemp()) / '.env'
        ruta.write_bytes('\ufeffUNA_VARIABLE_DE_PRUEBA=secreto\nOTRA_DE_PRUEBA=x\n'.encode('utf-8'))
        for k in ('UNA_VARIABLE_DE_PRUEBA', 'OTRA_DE_PRUEBA'):
            os.environ.pop(k, None)
        try:
            _cargar_env(ruta)
            self.assertEqual(os.environ.get('UNA_VARIABLE_DE_PRUEBA'), 'secreto')
            self.assertNotIn('\ufeffUNA_VARIABLE_DE_PRUEBA', os.environ)
        finally:
            for k in ('UNA_VARIABLE_DE_PRUEBA', 'OTRA_DE_PRUEBA', '\ufeffUNA_VARIABLE_DE_PRUEBA'):
                os.environ.pop(k, None)


if __name__ == '__main__':
    unittest.main()
