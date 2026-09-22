"""Transporte de WhatsApp (Meta Cloud API): verificación de firma, parseo del
envelope y envío saliente.

Es un adaptador puro de transporte: importa config y phone, NUNCA repo ni
pipeline. server.py sigue siendo el único lugar que conecta los dos lados.
"""
import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..config import config
from ..jscompat import a_json, texto_js
from ..phone import to_wa_id

# --- Entrada: firma ---------------------------------------------------------


def verificar_firma(raw: bytes, header: str | None) -> bool:
    """Verifica el header `X-Hub-Signature-256` de Meta sobre los BYTES CRUDOS del
    body (antes del parseo: re-serializar cambiaría el orden de claves y el
    escapado unicode, y el HMAC no coincidiría nunca).

    La clave es el App Secret de la app, NO el access token. Falla cerrado: sin
    META_APP_SECRET no se acepta ningún webhook.
    """
    if not config.meta_app_secret or not header:
        return False
    esperado = 'sha256=' + hmac.new(config.meta_app_secret.encode('utf-8'), raw, hashlib.sha256).hexdigest()
    # compare_digest no se va en tiempo con el contenido y tolera longitudes
    # distintas: un header corto devuelve 401, no rompe el handler.
    return hmac.compare_digest(esperado.encode('utf-8'), header.encode('utf-8'))


# --- Entrada: envelope ------------------------------------------------------


@dataclass
class MensajeEntrante:
    wa_message_id: str    # msg.id ('wamid.…'); '' si falta (curl de prueba)
    from_: str            # wa_id TAL CUAL lo mandó Meta — es a donde se responde
    phone_number_id: str  # número NUESTRO que lo recibió — es DESDE donde se responde
    waba_id: str          # `entry.id` = el WhatsApp Business Account ID (según Meta)
    tipo: str             # 'text' | 'audio' | 'image' | …
    texto: str            # '' si no es texto
    timestamp: str


def _obj(v: Any) -> dict[str, Any]:
    """Un nivel del envelope, o {} si no vino (equivale al `?.` encadenado)."""
    return v if isinstance(v, dict) else {}


def _lista(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _str(v: Any, por_defecto: str = '') -> str:
    return v if isinstance(v, str) else por_defecto


def extraer_entrantes(payload: Any) -> list[MensajeEntrante]:
    """Extrae los mensajes entrantes del envelope de Meta.

    Ignora `change.value.statuses`: los acuses (sent/delivered/read) llegan al
    mismo endpoint y no son mensajes. Cada mensaje que enviamos genera ~3, así que
    confundirlos multiplicaría el trabajo por cuatro.
    """
    out: list[MensajeEntrante] = []
    for entry in _lista(_obj(payload).get('entry')):
        for change in _lista(_obj(entry).get('changes')):
            # `metadata.phone_number_id` es el número NUESTRO al que le escribieron.
            # Una WABA puede tener varios (el de test que regala Meta + el propio) y
            # TODOS entran por este mismo webhook, indistinguibles salvo por acá.
            value = _obj(_obj(change).get('value'))
            metadata = _obj(value.get('metadata'))
            for msg in _lista(value.get('messages')):
                m = _obj(msg)
                out.append(MensajeEntrante(
                    wa_message_id=_str(m.get('id')),
                    from_=_str(m.get('from')),
                    phone_number_id=_str(metadata.get('phone_number_id')),
                    # `entry.id` es el WABA ID. Vale loguearlo: es el dato que hay que
                    # buscar a mano en el panel para chequear la suscripción de la app,
                    # y acá viene gratis con el primer mensaje que entra.
                    waba_id=_str(_obj(entry).get('id')),
                    tipo=_str(m.get('type'), 'text'),
                    texto=_str(_obj(m.get('text')).get('body')),
                    timestamp=_str(m.get('timestamp')),
                ))
    return out


# --- Salida: envío ----------------------------------------------------------


@dataclass
class ResultadoEnvio:
    ok: bool
    id: str | None = None
    error: str | None = None


MAX_TEXTO = 4096      # límite de WhatsApp para mensajes de texto
TIMEOUT_S = 10.0


def enviar_texto(
    to: str, cuerpo: str, responder_a: str | None = None, desde: str | None = None,
) -> ResultadoEnvio:
    """Envía un texto por la Cloud API.

    `to` debe ser el wa_id que mandó Meta, NO el teléfono guardado en la DB: la
    allow-list de los números de test hace match exacto, y devolverle a Meta su
    propio identificador esquiva el problema del 9 argentino (error 131030).
    """
    # `desde` es el phone_number_id que sale del webhook: se contesta SIEMPRE
    # desde el número al que le escribieron. Sin esto el bot respondía desde
    # META_PHONE_NUMBER_ID pasara lo que pasara, así que escribirle al número
    # propio dejaba ese chat mudo y la respuesta caía en el del número de test.
    numero_propio = desde or config.meta_phone_number_id
    faltan: list[str] = []
    if not config.meta_access_token:
        faltan.append('META_ACCESS_TOKEN')
    if not numero_propio:
        faltan.append('META_PHONE_NUMBER_ID')
    if faltan:
        print(f'[wa] no se puede enviar: falta {", ".join(faltan)}', file=sys.stderr)
        return ResultadoEnvio(ok=False, error=f'falta {", ".join(faltan)}')

    url = f'https://graph.facebook.com/{config.meta_graph_version}/{numero_propio}/messages'
    body: dict[str, Any] = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': to_wa_id(to),   # AR: saca el 9 que Meta manda en el `from` (si no, 131030)
        'type': 'text',
        'text': {'preview_url': False, 'body': cuerpo[:MAX_TEXTO]},
    }
    # Engancha la respuesta al mensaje original en el chat: hace mucho más claro
    # el ida y vuelta de la confirmación en el celular.
    if responder_a:
        body['context'] = {'message_id': responder_a}

    req = urllib.request.Request(
        url, data=a_json(body).encode('utf-8'), method='POST',
        headers={'Authorization': f'Bearer {config.meta_access_token}',
                 'Content-Type': 'application/json'})
    try:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
                status, crudo = res.status, res.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            status, crudo = e.code, e.read().decode('utf-8', 'replace')
        try:
            data = json.loads(crudo)
            if not isinstance(data, dict):
                data = {}
        except ValueError:
            data = {}

        if not 200 <= status < 300:
            # Loguear el código de Meta es lo que convierte 20 minutos de debug en 20
            # segundos. Los habituales: 131030 (destinatario fuera de la allow-list),
            # 190 (token vencido), 100 (phone_number_id mal), 131047 (fuera de 24 h).
            e = _obj(data.get('error'))
            details = _obj(e.get('error_data')).get('details')
            detalle = f' — {details}' if details else ''
            codigo = e.get('code')
            mensaje = e.get('message')
            print(f'[wa] error de Meta {status}: código {texto_js(codigo) if codigo is not None else "?"} · '
                  f'{mensaje if mensaje is not None else "sin mensaje"}{detalle}', file=sys.stderr)
            return ResultadoEnvio(ok=False, error=(
                f'{texto_js(codigo) if codigo is not None else status}: '
                f'{mensaje if mensaje is not None else "error"}'))

        mensajes = _lista(data.get('messages'))
        id_enviado = _obj(mensajes[0]).get('id') if mensajes else None
        print(f'[wa] → respuesta a {to_wa_id(to)} desde {numero_propio} '
              f'(id {id_enviado[:24] if isinstance(id_enviado, str) else "?"})')
        return ResultadoEnvio(ok=True, id=id_enviado)
    except Exception as err:
        # Nunca loguear headers ni el access token.
        print(f'[wa] fallo de red enviando a Meta: {err}', file=sys.stderr)
        return ResultadoEnvio(ok=False, error=str(err))
