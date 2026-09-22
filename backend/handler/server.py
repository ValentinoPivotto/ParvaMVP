"""Servidor HTTP con http.server (sin dependencias).

Sirve la web app, la API del dashboard y el webhook de Meta Cloud API, que es
por donde entran los mensajes reales de WhatsApp.
"""
import errno
import json
import os
import queue
import re
import socket
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from ..config import RAIZ, config, falta_config_meta
from ..formato import a_json, bindeable, numero, recortar
from ..repository import repo
from ..repository.db import init_schema
from ..repository.seed import seed_if_empty
from ..service.dashboard import build_state
from ..service.export import export_csv
from ..service.parser import parser_activo
from ..service.process import process_message
from .whatsapp import MensajeEntrante, enviar_texto, extraer_entrantes, verificar_firma

WEB_DIR = str(RAIZ / 'frontend')
MAX_BODY = 1024 * 1024  # 1 MB: los webhooks de Meta son chicos

TIPOS: dict[str, str] = {'html': 'text/html', 'css': 'text/css', 'js': 'text/javascript',
                         'json': 'application/json', 'svg': 'image/svg+xml'}

# El tamaño de un chunk son dígitos hexadecimales y nada más. `int(x, 16)` es
# más permisivo: acepta '-1', que pasa el control de tamaño y termina en un
# read(-1) — o sea, leer sin límite hasta EOF, y encima antes de verificar la
# firma. La validación va sobre el texto crudo, antes de convertir.
_TAMANO_CHUNK = re.compile(rb'^[0-9a-fA-F]+$')

# Lo mismo con el Content-Length, en decimal: una regex y no `isdigit()`, que
# acepta dígitos Unicode como '²' que después `int()` no puede convertir.
_LARGO = re.compile(r'^[0-9]+$')

# Una línea del encoding chunked tiene el mismo límite que `http.server` le pone
# a cada header. Sin límite, un cliente que nunca manda el fin de línea hace
# leer y guardar en memoria todo lo que quiera, antes de verificar la firma.
_MAX_LINEA = 65536

#: El cliente cortó la conexión. No es un error del server.
_CLIENTE_SE_FUE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class _PedidoInvalido(ValueError):
    """El cliente mandó algo mal formado.

    No es un error del server, así que va al log en una línea y sin traceback:
    el webhook está expuesto a internet y cualquiera podría llenar el archivo
    de tracebacks mandando pedidos rotos a repetición. La respuesta es la misma
    que para cualquier otro error.
    """


# --- Procesamiento asíncrono de mensajes de Meta ---------------------------

_colas: dict[str, queue.Queue] = {}
_colas_lock = threading.Lock()


def _drenar(clave: str, cola: queue.Queue) -> None:
    while True:
        with _colas_lock:
            if cola.empty():
                # Chequeo y baja bajo el mismo lock: si no, entre el "está
                # vacía" y el borrado alguien encola y ese trabajo queda sin
                # hilo que lo atienda.
                if _colas.get(clave) is cola:
                    del _colas[clave]
                return
        fn = cola.get()
        try:
            fn()
        except Exception:
            # Este catch es obligatorio: sin él la excepción muere con el hilo y
            # el mensaje queda sin respuesta y sin rastro.
            print(f'[wa] error procesando {clave}', file=sys.stderr)
            traceback.print_exc()


def encolar(clave: str, fn: Callable[[], None]) -> None:
    """Serializa el trabajo por remitente.

    Meta manda cada mensaje en su propio request HTTP, así que "compré gasoil" y
    el "sí" que lo confirma caen en callbacks independientes. Como el pipeline
    espera al parser (que puede ser una llamada al modelo), sin serializar pueden
    interleavearse y el "sí" no encontraría el pendiente todavía guardado
    ("No tengo nada pendiente para confirmar", intermitente y sin error).

    Un hilo por remitente activo, que se apaga cuando su cola queda vacía.
    """
    with _colas_lock:
        cola = _colas.get(clave)
        if cola is None:
            cola = queue.Queue()
            _colas[clave] = cola
            threading.Thread(target=_drenar, args=(clave, cola), daemon=True).start()
        cola.put(fn)


def manejar_entrante(m: MensajeEntrante) -> None:
    sender = repo.get_sender_by_telefono(m.from_)

    if not sender:
        # Este log es a la vez el diagnóstico (muestra el formato exacto que mandó
        # Meta, que es como se detecta el tema del 9 argentino) y el workflow de alta.
        print(f'[wa] número no registrado: {m.from_} — vinculalo con: '
              f'python3 -m backend.scripts.link_phone <usuarioId> +{m.from_}', file=sys.stderr)
        if config.meta_reply_to_unknown:
            enviar_texto(m.from_, 'Hola 👋 Este número no está registrado en Parva. '
                                  'Pedile a tu administrador que te dé de alta.', None, m.phone_number_id)
        return

    if m.tipo != 'text':
        enviar_texto(m.from_, 'Por ahora solo entiendo mensajes de texto 🙏 Las notas de voz llegan pronto.',
                     m.wa_message_id, m.phone_number_id)
        return
    if not recortar(m.texto):
        return

    # `or None` y no el id tal cual: extraer_entrantes usa '' cuando el envelope
    # no trae `msg.id` (un curl de prueba), y el índice único de wa_message_id
    # trata a '' como un valor — el segundo mensaje sin id se dedupearía solo.
    try:
        r = process_message(sender, m.texto, m.wa_message_id or None)
    except Exception:
        # Sin esto el error se lo comía la cola y el productor no recibía nada:
        # mandó un mensaje, no pasó nada, y no hay forma de que se entere. El
        # detalle va al log; al productor se le dice que reintente.
        print(f'[wa] error procesando el mensaje de {m.from_}', file=sys.stderr)
        traceback.print_exc()
        enviar_texto(m.from_, '😖 Se me rompió algo procesando ese mensaje. '
                              'Probá de nuevo, o escribímelo de otra forma.',
                     m.wa_message_id, m.phone_number_id)
        return
    if not r:
        print(f'[wa] duplicado ignorado: {m.wa_message_id}')
        return
    enviar_texto(m.from_, r.reply, m.wa_message_id, m.phone_number_id)


# --- HTTP -------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, formato: str, *args: Any) -> None:
        """Sin log por request.

        El servidor sirve estáticos y el dashboard le pega cada 5 s: un renglón
        por acceso enterraría el log que importa, que es el del bot
        (`[wa] ←`, `[wa] →`).
        """

    def send_response(self, code: int, message: str | None = None) -> None:
        """Igual que la de la clase base, pero sin el header `Server`.

        No aporta nada y publicaría la versión de Python en cada respuesta.
        """
        self.log_request(code)
        self.send_response_only(code, message)
        self.send_header('Date', self.date_time_string())

    # --- helpers de respuesta ---

    def _responder(self, code: int, cuerpo: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(cuerpo)))
        self.end_headers()
        # Un HEAD lleva los mismos headers que el GET, Content-Length incluido,
        # pero no el cuerpo.
        if self.command != 'HEAD':
            self.wfile.write(cuerpo)

    def _send_json(self, code: int, data: Any) -> None:
        self._responder(code, a_json(data).encode('utf-8'),
                        {'Content-Type': 'application/json; charset=utf-8'})

    def _servir_estatico(self, archivo: str) -> None:
        try:
            with open(os.path.join(WEB_DIR, archivo), 'rb') as f:
                buf = f.read()
        except OSError:
            self._responder(404, b'No encontrado', {'Content-Type': 'text/plain'})
            return
        ext = archivo.split('.')[-1] if '.' in archivo else 'html'
        self._responder(200, buf, {'Content-Type': f'{TIPOS.get(ext, "text/plain")}; charset=utf-8'})

    def _leer_body(self) -> bytes:
        """Lee el body como bytes.

        Se trabaja en bytes y no en texto: decodificar por partes rompe los
        caracteres multi-byte que caen en el borde de un chunk ("Compré" queda
        "Compr��") y, peor, cambia el HMAC — con acentos en casi todos
        los mensajes, la firma fallaría de forma intermitente según cómo TCP
        parta los paquetes.
        """
        if (self.headers.get('Transfer-Encoding') or '').lower() == 'chunked':
            chunks: list[bytes] = []
            size = 0
            while True:
                crudo = self._linea_de_chunk().split(b';')[0].strip()
                if not _TAMANO_CHUNK.match(crudo):
                    self.close_connection = True
                    raise _PedidoInvalido('tamaño de chunk inválido')
                largo = int(crudo, 16)
                if largo == 0:
                    self._linea_de_chunk()   # el CRLF que cierra el body
                    break
                size += largo
                if size > MAX_BODY:
                    self.close_connection = True
                    raise _PedidoInvalido('body demasiado grande')
                chunks.append(self.rfile.read(largo))
                self._linea_de_chunk()       # el CRLF que cierra el chunk
            return b''.join(chunks)

        crudo_largo = (self.headers.get('Content-Length') or '0').strip()
        if not _LARGO.match(crudo_largo):
            self.close_connection = True
            raise _PedidoInvalido('Content-Length inválido')
        # Las cifras se cuentan antes de convertir: `int()` no acepta más de
        # 4300, y con más de 9 el número ya supera el máximo. Los ceros de
        # adelante no cuentan: 0004 es 4.
        cifras = crudo_largo.lstrip('0')
        largo = int(cifras or '0') if len(cifras) <= 9 else MAX_BODY + 1
        if largo > MAX_BODY:
            self.close_connection = True
            raise _PedidoInvalido('body demasiado grande')
        return self.rfile.read(largo) if largo > 0 else b''

    def _linea_de_chunk(self) -> bytes:
        linea = self.rfile.readline(_MAX_LINEA + 1)
        if len(linea) > _MAX_LINEA:
            self.close_connection = True
            raise _PedidoInvalido('línea de chunk demasiado larga')
        return linea

    # --- ruteo ---

    def do_GET(self) -> None:
        self._manejar('GET')

    def do_POST(self) -> None:
        self._manejar('POST')

    def do_HEAD(self) -> None:
        """Un HEAD es un GET sin cuerpo: los monitores de uptime lo usan para
        preguntar si el server está vivo, y un 404 ahí lo daría por caído."""
        self._manejar('GET')

    def __getattr__(self, nombre: str) -> Any:
        """Cualquier otro método (PUT, DELETE, OPTIONS…) va al mismo ruteo y
        termina en el 404 de siempre, como cualquier ruta desconocida.

        `http.server` busca un `do_<MÉTODO>` para cada pedido, y si no lo
        encuentra contesta 501 con una página HTML propia, en inglés, que no se
        parece en nada al resto de las respuestas del server.
        """
        if nombre.startswith('do_'):
            return lambda: self._manejar(nombre[3:])
        raise AttributeError(nombre)

    def _manejar(self, method: str) -> None:
        partes = urlsplit(self.path)
        path = partes.path
        qs = {}
        for k, v in parse_qsl(partes.query, keep_blank_values=True):
            qs.setdefault(k, v)   # con la clave repetida, vale la primera

        def num_param(clave: str) -> Any:
            # Un parámetro ausente cuenta como 0; uno no numérico, como NULL.
            v = qs.get(clave)
            return bindeable(numero(v if v is not None else 0))

        enviado = False
        try:
            # --- Estáticos / web app ---
            if method == 'GET' and path in ('/', '/index.html'):
                enviado = True
                return self._servir_estatico('index.html')
            if method == 'GET' and path in ('/styles.css', '/app.js'):
                enviado = True
                return self._servir_estatico(path[1:])
            if method == 'GET' and path == '/healthz':
                enviado = True
                return self._send_json(200, {'ok': True})

            # --- API: productores y estado ---
            if method == 'GET' and path == '/api/productores':
                enviado = True
                return self._send_json(200, repo.list_productores())
            if method == 'GET' and path == '/api/state':
                state = build_state(num_param('productorId'))
                enviado = True
                return self._send_json(200, state) if state else \
                    self._send_json(404, {'error': 'productor no encontrado'})

            # --- API: export CSV ---
            if method == 'GET' and path == '/api/export':
                sheet = qs.get('sheet') if qs.get('sheet') is not None else 'movimientos'
                filename, content = export_csv(num_param('productorId'), sheet)
                enviado = True
                return self._responder(200, content.encode('utf-8'), {
                    'Content-Type': 'text/csv; charset=utf-8',
                    'Content-Disposition': f'attachment; filename="{filename}"',
                })

            # --- Webhook Meta Cloud API: verificación (GET) ---
            if method == 'GET' and path == '/webhook/whatsapp':
                mode = qs.get('hub.mode')
                token = qs.get('hub.verify_token')
                challenge = qs.get('hub.challenge')
                enviado = True
                if mode == 'subscribe' and token == config.meta_verify_token:
                    print('[wa] ✓ handshake verificado — Meta guardó la callback URL')
                    return self._responder(200, (challenge or '').encode('utf-8'),
                                           {'Content-Type': 'text/plain'})
                # 'null' y no 'None': es lo que imprime un parámetro ausente, y este
                # log se compara a ojo contra lo que muestra el panel de Meta.
                llego = 'null' if token is None else token
                print(f'[wa] ✗ handshake rechazado: verify_token no coincide '
                      f'(llegó "{llego}", esperaba "{config.meta_verify_token}")', file=sys.stderr)
                return self._responder(403, b'forbidden')

            # --- Webhook Meta Cloud API: ingreso de mensajes (POST) ---
            if method == 'POST' and path == '/webhook/whatsapp':
                raw = self._leer_body()

                # Sin firma válida no se procesa nada: el webhook está expuesto a
                # internet por el túnel y es la única puerta de entrada al pipeline.
                if not verificar_firma(raw, self.headers.get('X-Hub-Signature-256')):
                    print('[wa] firma inválida — descartado', file=sys.stderr)
                    enviado = True
                    return self._responder(401, 'firma inválida'.encode('utf-8'))

                try:
                    payload = json.loads(raw.decode('utf-8') or '{}')
                except ValueError:
                    enviado = True
                    return self._responder(400, 'json inválido'.encode('utf-8'))

                entrantes = extraer_entrantes(payload)

                # ACK primero: Meta reintenta durante días si el webhook tarda, y el
                # pipeline puede esperar al modelo. Recién después se procesa.
                enviado = True
                try:
                    self._send_json(200, {'status': 'received', 'procesados': len(entrantes)})
                    self.wfile.flush()
                except _CLIENTE_SE_FUE:
                    # Si la conexión se cortó antes del ack, lo que llegó se
                    # procesa igual: ya está completo y con la firma verificada.
                    # Meta va a reintentar, y el reintento se descarta por
                    # wa_message_id.
                    self.close_connection = True
                for m in entrantes:
                    # Qué número propio recibió el mensaje: es el dato que falta cuando la
                    # WABA tiene el de test y el propio y uno de los dos "no contesta".
                    print(f'[wa] ← {m.from_} → nuestro número {m.phone_number_id or "(sin metadata)"}'
                          f'{f" · WABA {m.waba_id}" if m.waba_id else ""}')
                    encolar(m.from_, lambda m=m: manejar_entrante(m))
                return

            enviado = True
            return self._responder(404, b'No encontrado', {'Content-Type': 'text/plain'})
        except _CLIENTE_SE_FUE:
            # El cliente cortó a mitad del pedido o de la respuesta: no hay a
            # quién mandarle un 500, y un traceback por cada pestaña cerrada
            # ensuciaría el log.
            self.close_connection = True
        except _PedidoInvalido as err:
            print(f'[http] pedido inválido: {err}', file=sys.stderr)
            if not enviado:
                self._send_json(500, {'error': str(err)})
        except Exception as err:
            print('Error:', err, file=sys.stderr)
            traceback.print_exc()
            # Con el ack rápido es fácil tirar después de haber mandado los headers;
            # sin este guard el error real quedaría tapado por un segundo intento
            # de responder sobre la misma conexión.
            if not enviado:
                self._send_json(500, {'error': str(err)})


class _Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Sin traceback cuando el cliente se fue.

        `socketserver` imprime un traceback por cualquier excepción que escape
        del handler, incluido un navegador que cierra la pestaña o ngrok
        cortando una conexión inactiva. En el log eso parece una falla y tapa
        las de verdad.
        """
        if isinstance(sys.exc_info()[1], _CLIENTE_SE_FUE):
            return
        super().handle_error(request, client_address)


class _ServidorDual(_Servidor):
    # Dual-stack sobre '::'. En macOS `localhost` resuelve primero a ::1, así
    # que un servidor solo-IPv4 dejaría afuera esa mitad.
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def _crear_servidor() -> ThreadingHTTPServer:
    try:
        return _ServidorDual(('::', config.port), _Handler)
    except OSError as e:
        # Con el puerto ocupado no se cae a IPv4: si el otro proceso escucha
        # sólo en IPv6, el bind en IPv4 funciona, y `localhost` —que en macOS
        # resuelve primero a ::1— le hablaría al otro proceso sin que nada avise.
        if e.errno == errno.EADDRINUSE:
            raise
        # Host sin IPv6: se cae a IPv4 en vez de no arrancar.
        return _Servidor(('0.0.0.0', config.port), _Handler)


def main() -> None:
    init_schema()
    seed_if_empty()
    try:
        server = _crear_servidor()
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        # El caso típico: quedó otro Parva corriendo en otra terminal. Sin este
        # mensaje, el traceback parece un problema de IPv6.
        print(f'✗ El puerto {config.port} ya está en uso: ¿quedó otro Parva corriendo? '
              f'Fijate con:  lsof -i :{config.port}', file=sys.stderr)
        sys.exit(1)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f'\n🌾 Parva MVP corriendo en http://localhost:{config.port}')
    # La mayoría de las fallas de setup son una env var faltante, y sin este
    # aviso el server arrancaría en silencio y simplemente nunca contestaría.
    faltan = falta_config_meta()
    if faltan:
        print(f'   ⚠️  FALTA CONFIGURAR: {", ".join(faltan)} — el bot no va a poder responder')
    else:
        print(f'   ✓ credenciales de Meta cargadas (Graph {config.meta_graph_version})')
    print('   webhook de Meta: /webhook/whatsapp  ·  estado: /api/state?productorId=1\n')
    # Va última porque puede tardar: si el parser está en modo local, el probe
    # de Ollama se lleva 800 ms y no tiene sentido demorar el resto del banner.
    print(f'   Parser activo: {parser_activo()}')

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
