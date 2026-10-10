"""Firma SigV4 para AWS, sin dependencias.

Bedrock no acepta una API key pelada en un header: cada request va firmado
con las credenciales (temporales, en el caso del SSO del curso).
Meter el SDK de AWS solo para esto traería medio árbol de dependencias y
rompería la premisa del repo, así que el algoritmo va acá. Está fijado por
AWS y no tiene variantes: es código que se escribe una vez y no se toca.
"""
import hashlib
import hmac as _hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

ALGO = 'AWS4-HMAC-SHA256'


@dataclass
class AwsCreds:
    access_key_id: str
    secret_access_key: str
    #: Presente solo en credenciales temporales (SSO/STS).
    session_token: str | None = None


@dataclass
class RequestFirmado:
    url: str
    headers: dict[str, str]
    body: str


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def _hmac_bytes(key: bytes, data: str) -> bytes:
    return _hmac.new(key, data.encode('utf-8'), hashlib.sha256).digest()


def _clave_de_firma(secret: str, fecha: str, region: str, servicio: str) -> bytes:
    """Clave derivada en cadena: secreto → fecha → región → servicio → aws4_request."""
    k_date = _hmac_bytes(('AWS4' + secret).encode('utf-8'), fecha)
    k_region = _hmac_bytes(k_date, region)
    k_service = _hmac_bytes(k_region, servicio)
    return _hmac_bytes(k_service, 'aws4_request')


def _path_canonico(path_enviado: str) -> str:
    """Path canónico: el que se firma NO es el que se envía.

    Para todo servicio que no sea S3, AWS percent-encodea de nuevo un path que ya
    viene encodeado. Un id de modelo como `amazon.nova-lite-v1:0` viaja en la URL
    como `...v1%3A0` y se firma como `...v1%253A0`. Verificado contra el
    canonical request que imprime botocore con `--debug`.

    `quote(safe='')` deja sin tocar exactamente los caracteres no reservados de
    RFC 3986 (A-Za-z0-9 - _ . ~), que es lo que pide la firma; las barras se
    reponen después porque separan segmentos y no se codifican.
    """
    return quote(path_enviado, safe='').replace('%2F', '/')


def firmar_aws(
    *,
    method: str,
    host: str,
    path: str,
    region: str,
    service: str,
    body: str,
    creds: AwsCreds,
    content_type: str = 'application/json',
    ahora: datetime | None = None,
) -> RequestFirmado:
    """Firma un POST y devuelve la URL, los headers y el body listos para el envío.

    `path` es el path tal como se envía, ya percent-encodeado por quien llama
    (los ids de modelo de Bedrock traen `:`). La doble codificación que exige la
    firma se aplica acá adentro.
    """
    now = ahora if ahora is not None else datetime.now(timezone.utc)

    # YYYYMMDDTHHMMSSZ
    amz_date = now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    fecha = amz_date[:8]

    # `host` se firma pero no se manda: lo pone el runtime desde la URL, y
    # mandarlo a mano lo duplicaría.
    firmados: dict[str, str] = {
        'content-type': content_type,
        'host': host,
        'x-amz-date': amz_date,
    }
    if creds.session_token:
        firmados['x-amz-security-token'] = creds.session_token

    nombres = sorted(firmados)
    canonical_headers = ''.join(f'{n}:{firmados[n].strip()}\n' for n in nombres)
    signed_headers = ';'.join(nombres)

    # method / uri / query / headers / signedHeaders / hash(payload).
    # El join intercala el \n que separa el bloque de headers (que ya termina
    # en \n) de la lista de signedHeaders.
    canonical_request = '\n'.join(
        [method, _path_canonico(path), '', canonical_headers, signed_headers, _sha256_hex(body)])

    scope = f'{fecha}/{region}/{service}/aws4_request'
    string_to_sign = '\n'.join([ALGO, amz_date, scope, _sha256_hex(canonical_request)])
    firma = _hmac_bytes(_clave_de_firma(creds.secret_access_key, fecha, region, service), string_to_sign).hex()

    headers = {n: v for n, v in firmados.items() if n != 'host'}
    headers['Authorization'] = (
        f'{ALGO} Credential={creds.access_key_id}/{scope}, '
        f'SignedHeaders={signed_headers}, Signature={firma}')
    return RequestFirmado(url=f'https://{host}{path}', headers=headers, body=body)
