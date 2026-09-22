"""Diagnóstico del setup de Meta Cloud API.

  python3 -m backend.scripts.check_meta
  python3 -m backend.scripts.check_meta <WABA_ID>

Contesta las tres preguntas detrás de "el número figura Conectado pero el bot
no responde": ¿a qué número apunta el .env?, ¿en qué WABA está?, ¿la app está
suscrita a ESA WABA? Cada falla imprime el arreglo concreto, porque los errores
de Meta son códigos numéricos sin contexto.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..config import config
from ..formato import percent_encode

G = f'https://graph.facebook.com/{config.meta_graph_version}'

# El mismo código de Meta significa cosas distintas según qué nodo pediste, así
# que los hints van por contexto. El 100 sobre una WABA es casi siempre haber
# pasado un phone_number_id, y el hint genérico decía justo lo contrario.
HINTS: dict[str, dict[int, str]] = {
    'numero': {
        100: 'ese ID no es un phone_number_id (¿pegaste el número de teléfono, o un WABA ID?)',
        190: 'access token vencido o revocado — el temporal del panel dura 24 h',
        200: 'el token no alcanza este número — creá uno de System User con la WABA asignada',
        133010: 'el número no está registrado en la Cloud API — falta el paso del PIN',
    },
    'waba': {
        100: 'ese ID no es una WABA — un phone_number_id no tiene edge `phone_numbers`',
        190: 'access token vencido o revocado — el temporal del panel dura 24 h',
        200: 'el token no alcanza esta WABA — creá uno de System User con la WABA asignada',
    },
}


def hint(ctx: str, code: int | None = None) -> str:
    h = HINTS[ctx].get(code) if code else None
    return f'\n     → {h}' if h else ''


@dataclass
class Respuesta:
    ok: bool
    data: Any = None
    code: int | None = None
    message: str | None = None


def graph(path: str, token: str | None = None) -> Respuesta:
    token = config.meta_access_token if token is None else token
    req = urllib.request.Request(f'{G}/{path}', headers={'Authorization': f'Bearer {token}'})
    try:
        try:
            with urllib.request.urlopen(req, timeout=10.0) as res:
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
            err = data.get('error') or {}
            return Respuesta(ok=False, code=err.get('code'), message=err.get('message') or f'HTTP {status}')
        return Respuesta(ok=True, data=data)
    except Exception as err:
        return Respuesta(ok=False, message=str(err))


# Los túneles gratis (trycloudflare, ngrok free) cambian de URL en CADA restart,
# y Meta se queda con la vieja: el webhook entrega a la nada y el bot enmudece
# sin un solo error visible de este lado.
EFIMEROS = ['trycloudflare.com', 'ngrok-free.app', 'ngrok-free.dev', 'ngrok.io', 'loca.lt']


def probar_handshake(url: str) -> tuple[bool, str]:
    """Le pega a la URL que Meta tiene guardada con el mismo handshake que usa Meta.

    Es la única forma de saber si el webhook llega: del lado del server, una URL
    muerta no produce ningún error — simplemente no pasa nada.
    """
    challenge = f'parva-{int(datetime.now().timestamp() * 1000)}'
    sep = '&' if '?' in url else '?'
    full = (f'{url}{sep}hub.mode=subscribe'
            f'&hub.verify_token={percent_encode(config.meta_verify_token)}'
            f'&hub.challenge={challenge}')
    try:
        try:
            with urllib.request.urlopen(full, timeout=10.0) as res:
                status, body = res.status, res.read().decode('utf-8', 'replace').strip()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read().decode('utf-8', 'replace').strip()
        if 200 <= status < 300 and body == challenge:
            return True, 'ok'
        # El 403 solo es nuestro si viene con el cuerpo que manda server.py; si no,
        # es algún intermediario (proxy, Cloudflare) y afirmar "verify token mal"
        # mandaría a buscar el problema al lugar equivocado.
        if status == 403 and body == 'forbidden':
            return False, ('el server contesta 403: el META_VERIFY_TOKEN que tiene corriendo '
                           'no coincide con el de este .env')
        extra = f' · {body[:60]}' if body else ''
        return False, (f'devuelve HTTP {status}{extra} — no es este server '
                       '(túnel caído, o algo en el medio)')
    except Exception as err:
        return False, f'no responde — {err}'


def main() -> None:
    problemas: list[str] = []
    # Lo que no se pudo chequear. Sin esto el veredicto daba "✅ Todo listo" tras
    # saltear la suscripción del webhook, que es la falla más común de todas.
    salteados: list[str] = []

    print(f'\n🔎 Chequeo de Meta Cloud API (Graph {config.meta_graph_version})\n')

    # --- 1. Variables de entorno --------------------------------------------
    faltantes = [k for k, v in (
        ('META_ACCESS_TOKEN', config.meta_access_token),
        ('META_PHONE_NUMBER_ID', config.meta_phone_number_id),
        ('META_APP_SECRET', config.meta_app_secret),
    ) if not str(v).strip()]

    if faltantes:
        print(f'✗ Faltan variables en .env: {", ".join(faltantes)}', file=sys.stderr)
        print('  Copiá .env.example a .env y completalas. Sin token no se puede chequear nada más.\n',
              file=sys.stderr)
        sys.exit(1)
    print('✓ .env completo')

    # --- 2. El número al que apunta el .env ---------------------------------
    num = graph(f'{config.meta_phone_number_id}'
                '?fields=display_phone_number,verified_name,quality_rating,code_verification_status')
    if not num.ok:
        print(f'✗ META_PHONE_NUMBER_ID={config.meta_phone_number_id} — {num.message} '
              f'(código {num.code if num.code is not None else "?"}){hint("numero", num.code)}\n',
              file=sys.stderr)
        sys.exit(1)
    d = num.data
    print(f'✓ META_PHONE_NUMBER_ID={config.meta_phone_number_id}')
    print(f'  {d.get("display_phone_number")} · nombre "{d.get("verified_name")}" · '
          f'calidad {d.get("quality_rating") or "n/d"} · {d.get("code_verification_status") or "n/d"}')

    es_test = re.sub(r'[^0-9+]', '', str(d.get('display_phone_number') or '')).startswith('+1555')
    if es_test:
        problemas.append(
            f'El .env apunta al número de TEST ({d.get("display_phone_number")}), no al tuyo.\n'
            '     Sólo habla con los 5 destinatarios allow-listeados y no se puede renombrar.\n'
            '     Cambiá META_PHONE_NUMBER_ID por el ID de tu número y volvé a correr esto.')

    # --- 3. La callback URL que Meta tiene guardada -------------------------
    # Un dominio ngrok RESERVADO vive en el mismo sufijo que los aleatorios, así que
    # el sufijo solo no distingue. Si la URL registrada usa el NGROK_DOMAIN del .env
    # es fija, y avisar "cambia en cada restart" mandaría a re-pegar algo que no hay
    # que tocar.
    dominio_fijo = re.sub(r'^https?://', '', (os.environ.get('NGROK_DOMAIN') or '').strip())

    if config.meta_app_id and config.meta_app_secret:
        app_token = f'{config.meta_app_id}|{config.meta_app_secret}'
        subs = graph(f'{config.meta_app_id}/subscriptions', app_token)
        lista_subs = (subs.data or {}).get('data') or []
        wa = next((x for x in lista_subs if x.get('object') == 'whatsapp_business_account'), None)
        if not subs.ok:
            salteados.append('la callback URL registrada (no pude leer las subscriptions de la app: '
                             f'{subs.message})')
        elif not wa:
            problemas.append('La app no tiene webhook de `whatsapp_business_account`. '
                             'Configuralo en WhatsApp → Configuration → Webhook.')
        else:
            campos = ', '.join(str(f.get('name') if isinstance(f, dict) else f) for f in (wa.get('fields') or []))
            print(f'✓ Webhook en Meta: {wa.get("callback_url")}')
            print(f'  campos: {campos or "(ninguno)"}{" · INACTIVO" if wa.get("active") is False else ""}')
            if 'messages' not in campos:
                problemas.append('El webhook no está suscrito al campo `messages`: '
                                 'verificar la URL es un paso, suscribirse es otro.')
            callback = str(wa.get('callback_url'))
            es_fijo = bool(dominio_fijo) and dominio_fijo in callback
            efimero = not es_fijo and any(h in callback for h in EFIMEROS)
            hs_ok, hs_detalle = probar_handshake(callback)
            if hs_ok:
                extra = (' (túnel efímero: cambia en cada restart, re-pegala cuando reinicies)'
                         if efimero else '')
                print(f'✓ Esa URL contesta el handshake ahora mismo{extra}')
            else:
                problemas.append(
                    f'La URL que Meta tiene guardada NO responde: {hs_detalle}\n'
                    '     Meta está entregando los webhooks a la nada y del lado del server no se ve ningún error.\n'
                    + ('     Es un túnel efímero: levantá cloudflared, copiá la URL NUEVA y re-pegala en\n'
                       '     WhatsApp → Configuration → Webhook (y revisá Manage → campo `messages`).'
                       if efimero else
                       '     Verificá que el server esté corriendo y que la URL sea alcanzable desde afuera.'))
    else:
        salteados.append('la callback URL registrada (falta META_APP_ID en .env)')

    # --- 4. La WABA que contiene ese número ---------------------------------
    # Por CLI/env, o preguntándole a Meta a qué WABA llega el token.
    wabas: list[str] = []
    arg_waba = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get('META_WABA_ID') or '').strip()

    if arg_waba:
        # Confusión habitual: pasar el phone_number_id donde va el WABA ID. Son dos
        # IDs numéricos largos indistinguibles a ojo, así que lo detectamos.
        quizas_numero = graph(f'{arg_waba}?fields=display_phone_number')
        if quizas_numero.ok and (quizas_numero.data or {}).get('display_phone_number'):
            problemas.append(
                f'{arg_waba} es un phone_number_id ({quizas_numero.data["display_phone_number"]}), '
                'no un WABA ID.\n'
                '     Ese va en META_PHONE_NUMBER_ID. El WABA ID está en WhatsApp Manager →\n'
                '     Configuración de la cuenta, o lo deduce solo si cargás META_APP_ID en .env.')
        else:
            wabas = [arg_waba]

    if not wabas and not arg_waba:
        # debug_token necesita un app access token (`APP_ID|APP_SECRET`); con el token
        # de usuario solo, Meta devuelve los scopes vacíos y no se deduce nada.
        if config.meta_app_id and config.meta_app_secret:
            app = percent_encode(f'{config.meta_app_id}|{config.meta_app_secret}')
            dbg = graph(f'debug_token?input_token={percent_encode(config.meta_access_token)}'
                        f'&access_token={app}')
            if not dbg.ok:
                print(f'  · No pude inspeccionar el token: {dbg.message}')
            else:
                info = (dbg.data or {}).get('data') or {}
                expires_at = info.get('expires_at')
                if expires_at == 0:
                    print('✓ Token permanente (no vence)')
                elif expires_at:
                    exp = datetime.fromtimestamp(expires_at)
                    marca = '✓' if exp > datetime.now() else '✗'
                    # 'es-AR': día/mes sin cero a la izquierda, hora con dos dígitos.
                    print(f'{marca} Token vence {exp.day}/{exp.month}/{exp.year}, {exp:%H:%M:%S}')
                # Un token de System User suele traer whatsapp_business_management además
                # de _messaging, y a veces la WABA sólo figura en uno de los dos.
                scopes = info.get('granular_scopes') or []
                objetivos: list[str] = []
                for x in scopes:
                    if str(x.get('scope')).startswith('whatsapp_business'):
                        objetivos.extend(x.get('target_ids') or [])
                wabas = list(dict.fromkeys(objetivos))
                if wabas:
                    print(f'✓ WABA que alcanza el token: {", ".join(wabas)}')
                else:
                    nombres = ', '.join(str(x.get('scope')) for x in scopes)
                    print(f'  · El token no expone ninguna WABA (scopes: {nombres or "ninguno"})')
        else:
            print('  · Para deducir la WABA sola, cargá META_APP_ID en .env (Settings → Basic).')

    if not wabas:
        salteados.append(
            'si la app está suscrita a la WABA del número (no pude averiguar qué WABA es).\n'
            '     El ID está en el panel de Meta → WhatsApp → API Setup, arriba de todo:\n'
            '     "WhatsApp Business Account ID". Después:  python3 -m backend.scripts.check_meta <WABA_ID>')
    else:
        dueña: str | None = None

        for waba in wabas:
            nums = graph(f'{waba}/phone_numbers?fields=id,display_phone_number,verified_name')
            if not nums.ok:
                print(f'  · WABA {waba}: no la puedo leer — {nums.message}{hint("waba", nums.code)}')
                continue
            lista = (nums.data or {}).get('data') or []
            tiene = any(n.get('id') == config.meta_phone_number_id for n in lista)
            if tiene:
                dueña = waba
            print(f'  · WABA {waba}: {len(lista)} número(s){" ← contiene el del .env" if tiene else ""}')
            for n in lista:
                marca = '▸' if n.get('id') == config.meta_phone_number_id else ' '
                print(f'      {marca} {n.get("id")}  {n.get("display_phone_number")}  "{n.get("verified_name")}"')

        if not dueña:
            problemas.append(
                f'Ninguna WABA visible contiene el número del .env ({config.meta_phone_number_id}). '
                'Si tenés más de una WABA, pasá la otra: python3 -m backend.scripts.check_meta <WABA_ID>')
        else:
            # --- 5. La suscripción, que es POR WABA y no se hereda de la de test ---
            subs = graph(f'{dueña}/subscribed_apps')
            apps = (subs.data or {}).get('data') or []
            if not subs.ok:
                problemas.append(f'No pude leer subscribed_apps de la WABA {dueña}: {subs.message}')
            elif not apps:
                problemas.append(
                    f'La app NO está suscrita a la WABA {dueña} — por eso no llega ni un mensaje. Arreglo:\n'
                    f'     curl -X POST "{G}/{dueña}/subscribed_apps" '
                    '-H "Authorization: Bearer $META_ACCESS_TOKEN"')
            else:
                nombres = ', '.join(
                    str((a.get('whatsapp_business_api_data') or {}).get('name') or a.get('id') or '?')
                    for a in apps)
                print(f'✓ App suscrita a la WABA {dueña}: {nombres}')

    # --- Veredicto ----------------------------------------------------------
    if problemas:
        print(f'\n⚠ {len(problemas)} cosa(s) para arreglar:\n')
        for p in problemas:
            print(f'  • {p}\n')
    if salteados:
        print('\n⚠ Chequeo INCOMPLETO — no pude verificar:\n')
        for p in salteados:
            print(f'  • {p}\n')
        print('  No es un OK: lo que no se chequeó es donde suele estar la falla.\n')
    if not problemas and not salteados:
        print('\n✅ Lado Meta OK. Escribile al número y mirá el log `[wa] ←` en la terminal.')
        # El otro lado del fallo silencioso: Meta entrega perfecto, pero si el
        # REMITENTE no está en la base el bot no contesta nada (META_REPLY_TO_UNKNOWN=0).
        print('   Falta un paso fuera de Meta: el número DESDE el que escribís tiene que')
        print('   estar vinculado en la base, o el bot lo ignora en silencio. Chequealo con:')
        print('     python3 -m backend.scripts.link_phone --list\n')
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
