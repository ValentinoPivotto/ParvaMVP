"""Los pedidos salientes: plazo total, redirecciones y errores legibles.

Se prueban contra un servidor local, así que no salen a internet.
"""
import http.server
import socket
import ssl
import threading
import time
import unittest

from backend.red import ErrorDeRed, describir, pedir

GOTA_S = 0.4          # un byte cada 0,4 s: 8 bytes tardan 3,2 s
PLAZO_S = 1.0


class _Servidor(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args) -> None:
        pass

    def _responder(self, status: int, cuerpo: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _gotear(self, con_largo: bool) -> None:
        self.send_response(200)
        if con_largo:
            self.send_header('Content-Length', '8')
        else:
            # Sin largo, el cuerpo termina cuando el servidor cierra.
            self.send_header('Connection', 'close')
        self.end_headers()
        try:
            for b in b'12345678':
                time.sleep(GOTA_S)
                self.wfile.write(bytes([b]))
                self.wfile.flush()
        except OSError:
            pass    # el cliente cortó: es justo lo que se está probando

    def do_GET(self) -> None:
        if self.path == '/redirige':
            self._responder(302, b'', {'Location': '/destino'})
        elif self.path == '/goteo':
            self._gotear(con_largo=True)
        elif self.path == '/goteo-sin-largo':
            self._gotear(con_largo=False)
        elif self.path == '/error':
            self._responder(500, b'se rompio')
        else:
            self._responder(200, f'GET {self.path}'.encode())

    def do_POST(self) -> None:
        cuerpo = self.rfile.read(int(self.headers['Content-Length']))
        if self.path == '/redirige':
            self._responder(302, b'', {'Location': '/destino'})
        else:
            self._responder(201, b'POST ' + cuerpo + b' ' + self.headers.get('Content-Type', '').encode())


class Pedir(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.servidor = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Servidor)
        cls.servidor.daemon_threads = True
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.servidor.server_address[1]}'

    @classmethod
    def tearDownClass(cls) -> None:
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def test_get_y_post(self) -> None:
        r = pedir('GET', f'{self.base}/hola?x=1', plazo=5)
        self.assertEqual((r.status, r.texto), (200, 'GET /hola?x=1'))
        r = pedir('POST', f'{self.base}/', plazo=5, cuerpo='{"a":1}',
                  headers={'Content-Type': 'application/json'})
        self.assertEqual((r.status, r.texto), (201, 'POST {"a":1} application/json'))

    def test_un_500_es_una_respuesta_y_no_una_excepcion(self) -> None:
        # Los que llaman leen el código de error de Meta del cuerpo: si esto
        # tirara, perderían el "código 190: token vencido".
        r = pedir('GET', f'{self.base}/error', plazo=5)
        self.assertFalse(r.ok)
        self.assertEqual((r.status, r.texto), (500, 'se rompio'))

    def test_un_get_sigue_la_redireccion_y_un_post_no(self) -> None:
        self.assertEqual(pedir('GET', f'{self.base}/redirige', plazo=5).texto, 'GET /destino')
        self.assertEqual(pedir('POST', f'{self.base}/redirige', plazo=5, cuerpo='x').status, 302)

    def _medir_corte(self, ruta: str) -> float:
        t0 = time.monotonic()
        with self.assertRaises(ErrorDeRed) as ctx:
            pedir('GET', f'{self.base}{ruta}', plazo=PLAZO_S)
        self.assertEqual(str(ctx.exception), 'tiempo agotado')
        return time.monotonic() - t0

    def test_el_plazo_es_total_aunque_lleguen_bytes(self) -> None:
        """Un byte cada 0,4 s nunca dispararía un timeout por lectura.

        Sin el plazo total, esto terminaba a los 3,2 s: lo que tardara el
        servidor en mandar todo, con el hilo esperando.
        """
        self.assertLess(self._medir_corte('/goteo'), PLAZO_S + 0.8)

    def test_el_plazo_se_cumple_tambien_sin_content_length(self) -> None:
        """El caso difícil: con `Connection: close`, http.client suelta el
        socket apenas lee los headers y el corte no tenía qué cortar."""
        self.assertLess(self._medir_corte('/goteo-sin-largo'), PLAZO_S + 0.8)

    def test_errores_de_red_con_mensaje_legible(self) -> None:
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        libre = s.getsockname()[1]
        s.close()
        with self.assertRaises(ErrorDeRed) as ctx:
            pedir('GET', f'http://127.0.0.1:{libre}/', plazo=3)
        self.assertEqual(str(ctx.exception), 'conexión rechazada')
        with self.assertRaises(ErrorDeRed) as ctx:
            pedir('GET', 'ftp://algo/', plazo=3)
        self.assertIn('URL inválida', str(ctx.exception))

    def test_un_certificado_que_no_se_verifica_dice_como_arreglarlo(self) -> None:
        # La falla típica del Python de python.org en macOS: sin esta pista, lo
        # único que se ve es que toda llamada a Meta y a Bedrock falla.
        e = ssl.SSLCertVerificationError(1, 'certificate verify failed')
        e.verify_message = 'unable to get local issuer certificate'
        self.assertIn('Install Certificates.command', describir(e))


if __name__ == '__main__':
    unittest.main()
