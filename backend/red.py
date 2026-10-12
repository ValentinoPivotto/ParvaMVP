"""Pedidos HTTP salientes: a Bedrock, a Ollama y a la Graph API de Meta.

Todos pasan por acá, por dos razones.

**Un plazo que es de verdad total.** El timeout de `urllib` —y el de
`http.client`— se aplica a cada lectura por separado: un servidor que contesta
de a un byte por segundo nunca lo dispara. Un modelo que se cuelga así dejaba
esperando la cola de ese remitente todo lo que quisiera, que es justo lo que el
timeout tenía que impedir. Acá el plazo cuenta desde que se abre la conexión
hasta el último byte: cuando se cumple, se corta el socket.

**Errores que se pueden leer.** Una falla de red llega como `ErrorDeRed`, con
un mensaje que dice qué pasó ("conexión rechazada", "tiempo agotado") en vez
del `<urlopen error ...>` de urllib. Y si es un certificado que no se puede
verificar, dice cómo arreglarlo, porque es la falla típica del Python del
instalador de python.org en macOS.

No usa proxies automáticamente: ni las variables `https_proxy` ni la
configuración de proxy del sistema.
"""
import http.client
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

_REDIRECCIONES = (301, 302, 303, 307, 308)
_MAX_REDIRECCIONES = 5


class ErrorDeRed(Exception):
    """No se pudo completar el pedido: DNS, conexión, TLS o plazo agotado."""


@dataclass
class Respuesta:
    """Lo que contestó el servidor. Un 4xx o 5xx es una respuesta, no un error."""

    status: int
    cuerpo: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def texto(self) -> str:
        return self.cuerpo.decode('utf-8', 'replace')


def pedir(metodo: str, url: str, *, plazo: float, headers: dict[str, str] | None = None,
          cuerpo: bytes | str | None = None) -> Respuesta:
    """Hace el pedido y devuelve la respuesta completa, o tira `ErrorDeRed`.

    `plazo` es el total en segundos, redirecciones incluidas. Las redirecciones
    se siguen sólo en un GET: los POST de este sistema (Bedrock, Ollama, el
    envío a Meta) nunca redirigen, y reenviar un cuerpo a otra URL sin que
    nadie lo haya decidido no es algo para hacer en silencio.
    """
    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode('utf-8')
    limite = time.monotonic() + plazo
    for _ in range(_MAX_REDIRECCIONES + 1):
        respuesta, destino = _un_pedido(metodo, url, headers or {}, cuerpo, limite)
        if destino is None:
            return respuesta
        url = destino
    raise ErrorDeRed('demasiadas redirecciones')


def _un_pedido(metodo: str, url: str, headers: dict[str, str], cuerpo: bytes | None,
               limite: float) -> tuple[Respuesta, str | None]:
    partes = urlsplit(url)
    if partes.scheme not in ('http', 'https') or not partes.hostname:
        raise ErrorDeRed(f'URL inválida: {url}')

    restante = _restante(limite)
    clase = http.client.HTTPSConnection if partes.scheme == 'https' else http.client.HTTPConnection
    con = clase(partes.hostname, partes.port, timeout=restante)

    # El vigía corta el socket cuando se cumple el plazo. `shutdown` destraba
    # una lectura que está esperando en otro hilo; el timeout del socket solo
    # no alcanza, porque se reinicia con cada byte que llega.
    cortado = threading.Event()
    # La referencia al socket se guarda aparte: si la respuesta trae
    # `Connection: close`, http.client suelta la suya (`con.sock = None`) apenas
    # lee los headers, y el vigía no tendría qué cortar mientras el cuerpo
    # sigue llegando de a gotas.
    socket_en_uso: list[socket.socket] = []

    def cortar() -> None:
        cortado.set()
        for sock in (*socket_en_uso, con.sock):
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    vigia = threading.Timer(restante, cortar)
    vigia.daemon = True
    vigia.start()
    try:
        ruta = (partes.path or '/') + (f'?{partes.query}' if partes.query else '')
        con.request(metodo, ruta, body=cuerpo, headers=headers)
        if con.sock is not None:
            socket_en_uso.append(con.sock)
        res = con.getresponse()
        datos = res.read()
        # Un servidor que no manda Content-Length termina su cuerpo cerrando la
        # conexión, así que el corte del vigía se vería como un cuerpo completo.
        # Por eso se mira el evento y no sólo si hubo excepción.
        if cortado.is_set():
            raise ErrorDeRed('tiempo agotado')
        ubicacion = res.getheader('Location')
        if metodo == 'GET' and res.status in _REDIRECCIONES and ubicacion:
            return Respuesta(res.status, datos), urljoin(url, ubicacion)
        return Respuesta(res.status, datos), None
    except ErrorDeRed:
        raise
    except (OSError, http.client.HTTPException) as e:
        if cortado.is_set():
            raise ErrorDeRed('tiempo agotado') from e
        raise ErrorDeRed(describir(e)) from e
    finally:
        vigia.cancel()
        con.close()


def _restante(limite: float) -> float:
    restante = limite - time.monotonic()
    if restante <= 0:
        raise ErrorDeRed('tiempo agotado')
    return restante


def describir(e: BaseException) -> str:
    """Una falla de red, en palabras."""
    if isinstance(e, ssl.SSLCertVerificationError):
        return (f'no se pudo verificar el certificado TLS ({e.verify_message}). Si usás el '
                'Python del instalador de python.org en macOS, corré "Install '
                'Certificates.command", en la carpeta de Python dentro de Aplicaciones')
    if isinstance(e, socket.gaierror):
        return 'no se pudo resolver el nombre del servidor'
    if isinstance(e, TimeoutError):
        return 'tiempo agotado'
    if isinstance(e, ConnectionRefusedError):
        return 'conexión rechazada'
    if isinstance(e, ConnectionResetError):
        return 'el servidor cortó la conexión'
    if isinstance(e, http.client.HTTPException):
        return f'respuesta HTTP inválida ({type(e).__name__})'
    if isinstance(e, OSError) and e.strerror:
        return e.strerror
    return str(e) or type(e).__name__
