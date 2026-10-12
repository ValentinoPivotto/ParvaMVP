"""El diagnóstico de Meta, contra una Graph API falsa.

`check_meta` es lo que se corre cuando el bot no contesta: si se equivoca,
manda a buscar el problema al lugar equivocado. Cada escenario reproduce una
de las fallas que el script sabe diagnosticar, y la salida entera queda en
`golden/check_meta.txt`.

La red se reemplaza en `check_meta.pedir`, así que ningún escenario sale a
internet ni usa credenciales reales: la configuración se fija en cada caso.
"""
import contextlib
import io
import json
import os
import re
import sys
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from backend.config import config
from backend.red import ErrorDeRed, Respuesta
from backend.scripts import check_meta

from .utiles import comparar_golden

NUMERO = '/PNID?fields=display_phone_number,verified_name'
NUMERO_OK = {'contiene': NUMERO, 'status': 200, 'json': {
    'display_phone_number': '+54 9 11 5555-1234', 'verified_name': 'Parva',
    'quality_rating': 'GREEN', 'code_verification_status': 'VERIFIED'}}
WEBHOOK_OK = {'contiene': '/APPID/subscriptions', 'status': 200, 'json': {'data': [{
    'object': 'whatsapp_business_account', 'callback_url': 'https://parva.ngrok-free.app/webhook/whatsapp',
    'fields': [{'name': 'messages'}], 'active': True}]}}

# (nombre, configuración, rutas de la Graph API falsa, argumentos)
ESCENARIOS = [
    ('falta el token', {'token': ''}, [], []),

    ('token vencido', {}, [
        {'contiene': NUMERO, 'status': 400,
         'json': {'error': {'code': 190, 'message': 'Error validating access token'}}}], []),

    ('todo en orden', {'ngrok': 'parva.ngrok-free.app'}, [
        NUMERO_OK, WEBHOOK_OK,
        {'contiene': 'parva.ngrok-free.app/webhook/whatsapp?', 'status': 200, 'eco': True},
        {'contiene': '/debug_token?', 'status': 200, 'json': {'data': {'expires_at': 0, 'granular_scopes': [
            {'scope': 'whatsapp_business_messaging', 'target_ids': ['WABA1']},
            {'scope': 'whatsapp_business_management', 'target_ids': ['WABA1']}]}}},
        {'contiene': '/WABA1/phone_numbers', 'status': 200, 'json': {'data': [
            {'id': 'PNID', 'display_phone_number': '+54 9 11 5555-1234', 'verified_name': 'Parva'}]}},
        {'contiene': '/WABA1/subscribed_apps', 'status': 200,
         'json': {'data': [{'whatsapp_business_api_data': {'name': 'parva-dev'}}]}}], []),

    ('número de test, sin webhook y WABA ajena', {}, [
        {'contiene': NUMERO, 'status': 200,
         'json': {'display_phone_number': '+1 555 123 4567', 'verified_name': 'Test Number'}},
        {'contiene': '/APPID/subscriptions', 'status': 200, 'json': {'data': [{'object': 'page'}]}},
        {'contiene': '/debug_token?', 'status': 200, 'json': {'data': {'expires_at': 1790000000, 'granular_scopes': [
            {'scope': 'whatsapp_business_messaging', 'target_ids': ['WABA2']}]}}},
        {'contiene': '/WABA2/phone_numbers', 'status': 200, 'json': {'data': [
            {'id': 'OTRO', 'display_phone_number': '+54 11 0000', 'verified_name': 'Otro'}]}}], []),

    ('túnel efímero, sin el campo messages y con 403', {}, [
        NUMERO_OK,
        {'contiene': '/APPID/subscriptions', 'status': 200, 'json': {'data': [{
            'object': 'whatsapp_business_account', 'callback_url': 'https://abc.trycloudflare.com/webhook/whatsapp',
            'fields': ['statuses'], 'active': False}]}},
        {'contiene': 'trycloudflare.com/webhook/whatsapp?', 'status': 403, 'texto': 'forbidden'},
        {'contiene': '/debug_token?', 'status': 400,
         'json': {'error': {'code': 100, 'message': 'Invalid OAuth access token'}}}], []),

    ('la WABA pasada es en realidad un número', {}, [
        NUMERO_OK,
        {'contiene': '/APPID/subscriptions', 'status': 500, 'json': {}},
        {'contiene': '/PNID2?fields=display_phone_number', 'status': 200,
         'json': {'display_phone_number': '+54 9 11 9999'}}], ['PNID2']),

    ('handshake caído y app sin suscribir', {'ngrok': 'parva.ngrok-free.app'}, [
        NUMERO_OK, WEBHOOK_OK,
        {'contiene': 'parva.ngrok-free.app/webhook/whatsapp?', 'error_de_red': 'conexión rechazada'},
        {'contiene': '/WABA3?fields=display_phone_number', 'status': 400, 'json': {'error': {'code': 100}}},
        {'contiene': '/WABA3/phone_numbers', 'status': 200, 'json': {'data': [
            {'id': 'PNID', 'display_phone_number': '+54 9 11 5555-1234', 'verified_name': 'Parva'}]}},
        {'contiene': '/WABA3/subscribed_apps', 'status': 200, 'json': {'data': []}}], ['WABA3']),

    ('sin META_APP_ID', {'app_id': ''}, [NUMERO_OK], []),

    ('un error de Meta que no es un objeto', {}, [
        {'contiene': NUMERO, 'status': 400, 'json': {'error': 'algo salió mal'}}], []),
]


def _graph_falsa(rutas: list[dict]):
    def pedir(metodo: str, url: str, *, plazo: float, headers=None, cuerpo=None) -> Respuesta:
        ruta = next((r for r in rutas if r['contiene'] in url), None)
        if ruta is None:
            raise AssertionError(f'el escenario no esperaba un pedido a {url}')
        if 'error_de_red' in ruta:
            raise ErrorDeRed(ruta['error_de_red'])
        if ruta.get('eco'):
            texto = parse_qs(urlsplit(url).query)['hub.challenge'][0]
        elif 'json' in ruta:
            texto = json.dumps(ruta['json'])
        else:
            texto = ruta.get('texto', '')
        return Respuesta(ruta['status'], texto.encode())
    return pedir


def _correr(conf: dict, rutas: list[dict], args: list[str]) -> tuple[int, str, str]:
    campos = {'meta_access_token': conf.get('token', 'TOKEN'), 'meta_phone_number_id': 'PNID',
              'meta_app_secret': 'secreto', 'meta_app_id': conf.get('app_id', 'APPID')}
    antes = {k: getattr(config, k) for k in campos}
    entorno_antes = {k: os.environ.get(k) for k in ('NGROK_DOMAIN', 'META_WABA_ID')}
    pedir_real, argv = check_meta.pedir, sys.argv

    for k, v in campos.items():
        setattr(config, k, v)
    os.environ['NGROK_DOMAIN'] = conf.get('ngrok', '')
    os.environ['META_WABA_ID'] = ''
    check_meta.pedir = _graph_falsa(rutas)
    sys.argv = ['check_meta', *args]

    salida, errores, codigo = io.StringIO(), io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
            check_meta.main()
    except SystemExit as e:
        codigo = e.code or 0
    finally:
        for k, v in antes.items():
            setattr(config, k, v)
        for k, v in entorno_antes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        check_meta.pedir, sys.argv = pedir_real, argv
    return codigo, salida.getvalue(), errores.getvalue()


class Diagnostico(unittest.TestCase):
    def test_cada_escenario(self) -> None:
        partes = []
        for nombre, conf, rutas, args in ESCENARIOS:
            codigo, salida, errores = _correr(conf, rutas, args)
            partes.append(f'### {nombre} · exit {codigo}\n--- stdout ---\n{salida}--- stderr ---\n{errores}')
        texto = '\n'.join(partes)
        # El vencimiento del token se muestra en hora local: depende de la zona
        # horaria de quien corre el test. El formato se prueba aparte.
        texto = re.sub(r'Token vence \d{1,2}/\d{1,2}/\d{4}, \d{2}:\d{2}:\d{2}',
                       'Token vence {FECHA LOCAL}', texto)
        comparar_golden(self, 'check_meta.txt', texto)

    def test_sale_con_error_salvo_que_todo_este_en_orden(self) -> None:
        # Un chequeo salteado nunca se reporta como OK.
        for nombre, conf, rutas, args in ESCENARIOS:
            with self.subTest(escenario=nombre):
                esperado = 0 if nombre == 'todo en orden' else 1
                self.assertEqual(_correr(conf, rutas, args)[0], esperado)

    def test_un_error_de_red_se_lee_sin_envoltorios(self) -> None:
        nombre, conf, rutas, args = next(e for e in ESCENARIOS if e[0] == 'handshake caído y app sin suscribir')
        salida = _correr(conf, rutas, args)[1]
        self.assertIn('no responde — conexión rechazada', salida)
        self.assertNotIn('urlopen', salida)

    @unittest.skipUnless(hasattr(time, 'tzset'), 'time.tzset() no existe en esta plataforma')
    def test_el_vencimiento_del_token_sale_en_formato_argentino(self) -> None:
        # Día y mes sin cero adelante, hora con dos dígitos: 21/9/2026, 11:13:20.
        tz_antes = os.environ.get('TZ')
        os.environ['TZ'] = 'America/Argentina/Buenos_Aires'
        time.tzset()
        try:
            _, conf, rutas, args = next(e for e in ESCENARIOS if e[0].startswith('número de test'))
            salida = _correr(conf, rutas, args)[1]
        finally:
            if tz_antes is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = tz_antes
            time.tzset()
        self.assertIn('✗ Token vence 21/9/2026, 11:13:20', salida)


if __name__ == '__main__':
    unittest.main()
