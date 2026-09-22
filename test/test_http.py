"""Las respuestas HTTP del servidor, tal como salen por el socket.

No alcanza con comprobar el cuerpo: el frontend y Meta dependen también del
status y de los headers (el `Content-Type` de cada estático, el
`Content-Disposition` del CSV, el 403 pelado del handshake rechazado). Por eso
se congela la respuesta cruda.

El servidor se levanta en proceso, en un puerto efímero (`PORT=0`, que fija
`test/__init__.py`), así que el test no pisa una instancia que estés corriendo.
"""
import contextlib
import errno
import hashlib
import hmac
import http.client
import io
import socket
import struct
import threading
import time
import unittest
from typing import Callable

from backend.config import config
from backend.handler.server import _crear_servidor
from backend.formato import a_json

from .utiles import base_limpia, comparar_golden

# Headers que cambian en cada corrida o dependen del transporte.
IGNORADOS = {'date', 'connection', 'keep-alive', 'transfer-encoding', 'content-length', 'server'}

CUERPO_WEBHOOK = a_json({'entry': [{'id': 'WABA_TEST', 'changes': [{'value': {
    'metadata': {'phone_number_id': 'NUM_TEST'},
    'messages': [{'id': 'wamid.prueba.http', 'from': '5491100000999', 'type': 'text',
                  'text': {'body': 'Compré 200 litros de gasoil'}}]}}]}]})


def _firmar(cuerpo: str) -> str:
    return 'sha256=' + hmac.new(config.meta_app_secret.encode('utf-8'),
                                cuerpo.encode('utf-8'), hashlib.sha256).hexdigest()


class RespuestasHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base_limpia()
        cls.servidor = _crear_servidor()
        cls.puerto = cls.servidor.server_address[1]
        cls.hilo = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def pedir(self, metodo: str, ruta: str, cuerpo: str | None = None,
              headers: dict[str, str] | None = None) -> str:
        con = http.client.HTTPConnection('127.0.0.1', self.puerto, timeout=10)
        try:
            # UTF-8 explícito: http.client codifica un str en latin-1 y eso
            # rompe los acentos — y con ellos el HMAC del webhook, que se
            # calcula sobre los bytes crudos.
            con.request(metodo, ruta, body=cuerpo.encode('utf-8') if cuerpo else None,
                        headers=headers or {})
            res = con.getresponse()
            lineas = [f'HTTP/1.1 {res.status} {res.reason}']
            for k, v in sorted(res.getheaders()):
                if k.lower() not in IGNORADOS:
                    lineas.append(f'{k}: {v}')
            cuerpo_res = res.read().decode('utf-8', 'replace')
            return '\n'.join(lineas) + '\n\n' + cuerpo_res
        finally:
            con.close()

    def test_todas_las_rutas(self) -> None:
        token = config.meta_verify_token
        pedidos: list[tuple[str, str, str | None, dict[str, str] | None]] = [
            ('GET', '/', None, None),
            ('GET', '/index.html', None, None),
            ('GET', '/styles.css', None, None),
            ('GET', '/app.js', None, None),
            ('GET', '/healthz', None, None),
            ('GET', '/api/productores', None, None),
            ('GET', '/api/state?productorId=1', None, None),
            ('GET', '/api/state?productorId=2', None, None),
            # Inexistente, ausente y basura: los tres tienen que dar 404, no 500.
            ('GET', '/api/state?productorId=99', None, None),
            ('GET', '/api/state', None, None),
            ('GET', '/api/state?productorId=abc', None, None),
            ('GET', '/api/export?productorId=1&sheet=movimientos', None, None),
            ('GET', '/api/export?productorId=1&sheet=hacienda', None, None),
            ('GET', '/api/export?productorId=2&sheet=margenes', None, None),
            ('GET', '/api/export?productorId=1', None, None),
            ('GET', '/noexiste', None, None),
            ('POST', '/noexiste', None, None),
            # Handshake de Meta: bien, con token mal, y sin parámetros.
            ( 'GET', f'/webhook/whatsapp?hub.mode=subscribe&hub.verify_token={token}'
                     '&hub.challenge=desafio123', None, None),
            ('GET', '/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=malo&hub.challenge=x',
             None, None),
            ('GET', '/webhook/whatsapp', None, None),
            # Webhook: firma mala, sin firma, firma buena con json roto, y válido.
            ('POST', '/webhook/whatsapp', CUERPO_WEBHOOK,
             {'Content-Type': 'application/json', 'X-Hub-Signature-256': 'sha256=deadbeef'}),
            ('POST', '/webhook/whatsapp', CUERPO_WEBHOOK, {'Content-Type': 'application/json'}),
            ('POST', '/webhook/whatsapp', 'no soy json',
             {'Content-Type': 'application/json', 'X-Hub-Signature-256': _firmar('no soy json')}),
            ('POST', '/webhook/whatsapp', CUERPO_WEBHOOK,
             {'Content-Type': 'application/json', 'X-Hub-Signature-256': _firmar(CUERPO_WEBHOOK)}),
        ]
        partes = []
        for metodo, ruta, cuerpo, headers in pedidos:
            partes.append(f'### {metodo} {ruta}\n{self.pedir(metodo, ruta, cuerpo, headers)}')
        comparar_golden(self, 'http.txt', '\n'.join(partes))

    def test_el_webhook_falla_cerrado_sin_firma(self) -> None:
        # El endpoint está expuesto a internet por el túnel: es la única puerta
        # de entrada al pipeline y sin firma válida no se procesa nada.
        for headers in ({'X-Hub-Signature-256': 'sha256=deadbeef'}, {}, {'X-Hub-Signature-256': 'x'}):
            with self.subTest(headers=headers):
                self.assertIn('401', self.pedir('POST', '/webhook/whatsapp',
                                                CUERPO_WEBHOOK, headers).splitlines()[0])

    def test_una_firma_corta_da_401_y_no_rompe(self) -> None:
        # Comparar hashes de distinta longitud tiraba una excepción y el handler
        # moría en vez de contestar 401.
        self.assertIn('401', self.pedir('POST', '/webhook/whatsapp', CUERPO_WEBHOOK,
                                        {'X-Hub-Signature-256': 'a'}).splitlines()[0])

    def test_el_body_gigante_no_tumba_el_server(self) -> None:
        # El límite es 1 MB. El server mira el Content-Length y rechaza ANTES de
        # leer el cuerpo, así que puede cortar la conexión mientras el cliente
        # todavía está mandando: recibir el 500 o que se corte la conexión son
        # las dos un rechazo válido. Lo que importa es que el server siga en pie
        # y que no se haya tragado el megabyte.
        grande = 'a' * (1024 * 1024 + 10)
        try:
            respuesta = self.pedir('POST', '/webhook/whatsapp', grande,
                                   {'Content-Type': 'text/plain'})
            self.assertIn('500', respuesta.splitlines()[0])
            self.assertIn('body demasiado grande', respuesta)
        except (BrokenPipeError, ConnectionResetError, http.client.RemoteDisconnected):
            pass
        self.assertIn('200', self.pedir('GET', '/healthz').splitlines()[0])

    def test_el_ack_del_webhook_no_espera_al_pipeline(self) -> None:
        # Meta reintenta durante días si el webhook tarda, y el pipeline puede
        # quedarse esperando al modelo: primero se contesta, después se procesa.
        cuerpo = CUERPO_WEBHOOK.replace('wamid.prueba.http', 'wamid.ack.1')
        respuesta = self.pedir('POST', '/webhook/whatsapp', cuerpo,
                               {'X-Hub-Signature-256': _firmar(cuerpo)})
        self.assertIn('{"status":"received","procesados":1}', respuesta)


class RedDeSeguridad(unittest.TestCase):
    """Un mensaje nunca se queda sin respuesta.

    El pipeline corre desprendido del request (el webhook ya contestó 200), así
    que una excepción ahí adentro no la ve nadie: el productor escribe, no pasa
    nada y no tiene forma de enterarse.
    """

    def test_si_el_pipeline_explota_el_productor_igual_recibe_algo(self) -> None:
        from backend.handler import server
        from backend.handler.whatsapp import MensajeEntrante

        base_limpia()
        enviados: list[str] = []

        def explotar(*_a, **_k):
            raise RuntimeError('falla simulada en el pipeline')

        proceso, envio = server.process_message, server.enviar_texto
        server.process_message = explotar
        server.enviar_texto = lambda to, cuerpo, *a, **k: enviados.append(cuerpo)
        try:
            server.manejar_entrante(MensajeEntrante(
                wa_message_id='wamid.red.1', from_='5491100000001', phone_number_id='N',
                waba_id='W', tipo='text', texto='compré 200 litros de gasoil', timestamp=''))
        finally:
            server.process_message, server.enviar_texto = proceso, envio

        self.assertEqual(len(enviados), 1, 'tendría que haber contestado exactamente una vez')
        self.assertIn('Se me rompió algo', enviados[0])


class OtrosMetodos(unittest.TestCase):
    """GET y POST no son los únicos métodos que llegan.

    Sin un `do_<MÉTODO>`, `http.server` contesta 501 con una página HTML
    propia, en inglés. Los monitores de uptime preguntan con HEAD: un 501 ahí
    da al server por caído.
    """

    @classmethod
    def setUpClass(cls) -> None:
        base_limpia()
        cls.servidor = _crear_servidor()
        cls.puerto = cls.servidor.server_address[1]
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def _crudo(self, metodo: str, ruta: str) -> tuple[list[bytes], bytes]:
        """Los headers y todo lo que viene después, tal cual llegan por el socket.

        Por el socket y no con http.client, que en un HEAD no lee el cuerpo
        aunque el server lo mande.
        """
        with socket.create_connection(('127.0.0.1', self.puerto), timeout=5) as s:
            s.sendall(f'{metodo} {ruta} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n'.encode())
            recibido = b''
            while parte := s.recv(65536):
                recibido += parte
        headers, _, cuerpo = recibido.partition(b'\r\n\r\n')
        return [h for h in headers.split(b'\r\n') if not h.lower().startswith(b'date:')], cuerpo

    def test_head_es_un_get_sin_cuerpo(self) -> None:
        for ruta in ('/healthz', '/', '/api/state?productorId=1', '/api/export?productorId=1'):
            with self.subTest(ruta=ruta):
                headers_get, cuerpo_get = self._crudo('GET', ruta)
                headers_head, cuerpo_head = self._crudo('HEAD', ruta)
                self.assertTrue(cuerpo_get)
                self.assertEqual(headers_head, headers_get)     # Content-Length incluido
                self.assertEqual(cuerpo_head, b'')

    def test_cualquier_otro_metodo_es_el_404_de_siempre(self) -> None:
        for metodo in ('PUT', 'DELETE', 'OPTIONS', 'PATCH', 'TRACE', 'PROPFIND'):
            with self.subTest(metodo=metodo):
                con = http.client.HTTPConnection('127.0.0.1', self.puerto, timeout=10)
                try:
                    con.request(metodo, '/api/state')
                    res = con.getresponse()
                    self.assertEqual((res.status, res.getheader('Content-Type'), res.read()),
                                     (404, 'text/plain', 'No encontrado'.encode()))
                finally:
                    con.close()


class ClientesQueSeVan(unittest.TestCase):
    """Un cliente que corta la conexión no es un error del server.

    Una pestaña que se cierra, o ngrok cortando una conexión inactiva, no puede
    dejar un traceback en el log: parece una falla y tapa las de verdad.
    """

    @classmethod
    def setUpClass(cls) -> None:
        base_limpia()
        cls.servidor = _crear_servidor()
        cls.puerto = cls.servidor.server_address[1]
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def _log_del_server(self, cliente: Callable[[], None]) -> str:
        """Lo que el server escribe en stderr mientras atiende a `cliente`."""
        hilos = threading.active_count()
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            cliente()
            # Hasta que termine el hilo que atendía la conexión.
            limite = time.monotonic() + 5
            while threading.active_count() > hilos and time.monotonic() < limite:
                time.sleep(0.01)
        return log.getvalue()

    def _conectar(self) -> socket.socket:
        return socket.create_connection(('127.0.0.1', self.puerto), timeout=5)

    @staticmethod
    def _resetear(s: socket.socket) -> None:
        # SO_LINGER en 0: el close manda un reset en vez de un cierre prolijo.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        s.close()

    def test_entre_un_pedido_y_el_siguiente(self) -> None:
        def cliente() -> None:
            s = self._conectar()
            s.sendall(b'GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n')
            s.recv(500)     # contesta y queda esperando el pedido siguiente
            self._resetear(s)

        self.assertNotIn('Traceback', self._log_del_server(cliente))

    def test_a_mitad_del_cuerpo(self) -> None:
        def cliente() -> None:
            s = self._conectar()
            s.sendall(b'POST /webhook/whatsapp HTTP/1.1\r\nHost: x\r\n'
                      b'Content-Length: 1000\r\n\r\n{"entry": [')
            time.sleep(0.3)     # que el server ya esté esperando el resto
            self._resetear(s)

        log = self._log_del_server(cliente)
        self.assertNotIn('Traceback', log)
        self.assertNotIn('Error', log)
        con = http.client.HTTPConnection('127.0.0.1', self.puerto, timeout=5)
        try:
            con.request('GET', '/healthz')
            self.assertEqual(con.getresponse().status, 200)
        finally:
            con.close()

    def test_si_se_va_antes_del_ack_el_mensaje_se_procesa_igual(self) -> None:
        """El cuerpo ya llegó completo y con la firma verificada: perderlo
        porque falló el ack deja al productor sin respuesta hasta el reintento
        de Meta, que puede tardar."""
        from backend.handler import server

        encolados: list[str] = []
        responder, encolar = server._Handler._responder, server.encolar

        def responder_sin_cliente(handler, code, cuerpo, headers=None):
            if code == 200 and handler.path == '/webhook/whatsapp':
                raise BrokenPipeError(32, 'Broken pipe')
            return responder(handler, code, cuerpo, headers)

        server._Handler._responder = responder_sin_cliente
        server.encolar = lambda clave, fn: encolados.append(clave)
        cuerpo = CUERPO_WEBHOOK.replace('wamid.prueba.http', 'wamid.sin.ack')

        def cliente() -> None:
            con = http.client.HTTPConnection('127.0.0.1', self.puerto, timeout=5)
            try:
                con.request('POST', '/webhook/whatsapp', body=cuerpo.encode(),
                            headers={'X-Hub-Signature-256': _firmar(cuerpo)})
                with self.assertRaises(http.client.RemoteDisconnected):
                    con.getresponse()
            finally:
                con.close()

        try:
            log = self._log_del_server(cliente)
        finally:
            server._Handler._responder, server.encolar = responder, encolar
        self.assertEqual(encolados, ['5491100000999'])
        self.assertNotIn('Traceback', log)


class PuertoOcupado(unittest.TestCase):
    """Si el puerto ya está tomado, el server lo tiene que decir y no arrancar."""

    def setUp(self) -> None:
        self.puerto_antes = config.port
        self.addCleanup(setattr, config, 'port', self.puerto_antes)

    def test_dos_servidores_en_el_mismo_puerto_chocan(self) -> None:
        primero = _crear_servidor()
        self.addCleanup(primero.server_close)
        config.port = primero.server_address[1]
        with self.assertRaises(OSError) as ctx:
            _crear_servidor().server_close()
        self.assertEqual(ctx.exception.errno, errno.EADDRINUSE)

    def test_no_cae_a_ipv4_si_el_otro_escucha_solo_en_ipv6(self) -> None:
        """El bind en IPv4 funcionaría, y `localhost` —que en macOS resuelve
        primero a ::1— le hablaría al otro proceso sin que nada avise."""
        otro = socket.socket(socket.AF_INET6)
        self.addCleanup(otro.close)
        otro.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        otro.bind(('::', 0))
        otro.listen()
        config.port = otro.getsockname()[1]
        with self.assertRaises(OSError) as ctx:
            _crear_servidor().server_close()
        self.assertEqual(ctx.exception.errno, errno.EADDRINUSE)

    def test_sin_ipv6_cae_a_ipv4(self) -> None:
        from backend.handler import server

        def sin_ipv6(*_a, **_k):
            raise OSError(errno.EAFNOSUPPORT, 'Address family not supported by protocol')

        clase = server._ServidorDual
        server._ServidorDual = sin_ipv6
        try:
            servidor = _crear_servidor()
        finally:
            server._ServidorDual = clase
        self.addCleanup(servidor.server_close)
        self.assertEqual(servidor.server_address[0], '0.0.0.0')

    def test_el_arranque_lo_dice_en_una_linea(self) -> None:
        from backend.handler import server

        def ocupado():
            raise OSError(errno.EADDRINUSE, 'Address already in use')

        crear = server._crear_servidor
        server._crear_servidor = ocupado
        errores = io.StringIO()
        try:
            with contextlib.redirect_stderr(errores), self.assertRaises(SystemExit) as ctx:
                server.main()
        finally:
            server._crear_servidor = crear
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(errores.getvalue(), f'✗ El puerto {config.port} ya está en uso: ¿quedó otro '
                                             f'Parva corriendo? Fijate con:  lsof -i :{config.port}\n')


if __name__ == '__main__':
    unittest.main()
