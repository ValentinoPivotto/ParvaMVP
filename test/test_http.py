"""Las respuestas HTTP del servidor, tal como salen por el socket.

No alcanza con comprobar el cuerpo: el frontend y Meta dependen también del
status y de los headers (el `Content-Type` de cada estático, el
`Content-Disposition` del CSV, el 403 pelado del handshake rechazado). Por eso
se congela la respuesta cruda.

El servidor se levanta en proceso, en un puerto efímero (`PORT=0`, que fija
`test/__init__.py`), así que el test no pisa una instancia que estés corriendo.
"""
import hashlib
import hmac
import http.client
import threading
import unittest

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


if __name__ == '__main__':
    unittest.main()
