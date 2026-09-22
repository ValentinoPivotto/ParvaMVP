"""Formato de números, fechas y JSON.

Son decisiones chicas con efecto visible: si alguna cambia, cambian los montos
que ve el productor en WhatsApp, los que salen en el CSV o el día con el que
queda fechado un registro. El contrato queda fijado acá.
"""
import math
import time
import unittest
from datetime import datetime, timedelta, timezone

from backend.formato import (a_json, bindeable, percent_encode, fecha_iso,
                              miles, numero, pesos, texto_numero)


class Numeros(unittest.TestCase):
    def test_leer_un_numero_de_un_texto(self) -> None:
        self.assertEqual(numero('3000'), 3000)
        self.assertEqual(numero(None, 3000), 3000)     # ausente → el default
        self.assertEqual(numero(''), 0)                # vacío → 0
        self.assertEqual(numero('0.7'), 0.7)
        self.assertEqual(numero('  12  '), 12)
        self.assertTrue(math.isnan(numero('abc')))
        # `float()` de Python las aceptaría como números; acá no lo son.
        for basura in ('1_000', 'inf', 'nan', 'infinity'):
            with self.subTest(basura=basura):
                self.assertTrue(math.isnan(numero(basura)))

    def test_nan_se_bindea_como_null(self) -> None:
        # Un ?productorId= basura tiene que terminar en un SELECT que no
        # matchea nada — 404 — y no en un 500.
        self.assertIsNone(bindeable(numero('abc')))
        self.assertEqual(bindeable(7), 7)

    def test_un_monto_entero_se_escribe_sin_decimal(self) -> None:
        # SQLite devuelve las columnas REAL como float: sin esto, cada monto
        # del CSV y de los mensajes del bot saldría como "1200000.0".
        self.assertEqual(texto_numero(1200000.0), '1200000')
        self.assertEqual(texto_numero(100.5), '100.5')
        self.assertEqual(texto_numero(5), '5')
        self.assertEqual(texto_numero(None), '')
        self.assertEqual(texto_numero(True), 'true')   # en Python un bool es int

    def test_los_montos_redondean_los_cinco_para_arriba(self) -> None:
        # round() de Python redondea al par: round(2.5) es 2, y acá tiene que
        # ser 3.
        self.assertEqual(pesos(2.5), '$3')
        self.assertEqual(pesos(-0.5), '$0')
        self.assertEqual(pesos(-1.5), '$-1')
        self.assertEqual(pesos(9600000), '$9.600.000')
        self.assertEqual(pesos(-1234567.4), '$-1.234.567')
        self.assertEqual(miles(1000), '1.000')
        self.assertEqual(miles(100), '100')


class Serializacion(unittest.TestCase):
    def test_el_json_sale_compacto_y_con_acentos_literales(self) -> None:
        self.assertEqual(
            a_json({'a': 200.0, 'b': 2.5, 'c': 'ñ é', 'd': True, 'e': None, 'f': [1.0, 2.25]}),
            '{"a":200,"b":2.5,"c":"ñ é","d":true,"e":null,"f":[1,2.25]}')

    def test_el_json_no_emite_NaN(self) -> None:
        # json.dumps escupiría NaN, que no es JSON válido y le rompería el
        # parseo al frontend.
        self.assertEqual(a_json({'x': float('nan'), 'y': float('inf')}), '{"x":null,"y":null}')

    def test_percent_encode(self) -> None:
        # El id de modelo de Bedrock trae ':' y tiene que viajar entero como un
        # solo segmento del path; la firma SigV4 depende de esto.
        self.assertEqual(percent_encode('amazon.nova-lite-v1:0'), 'amazon.nova-lite-v1%3A0')
        self.assertEqual(percent_encode("a b/c'd(e)"), "a%20b%2Fc'd(e)")


class Fechas(unittest.TestCase):
    def test_el_dia_se_mueve_local_y_se_informa_en_utc(self) -> None:
        # El offset se aplica en hora local y recién después se pasa a UTC. En
        # Argentina eso significa que después de las 21:00 la fecha guardada ya
        # es la de mañana: es la convención con la que están fechados los
        # registros existentes.
        for offset in (0, -1, -2, -120):
            with self.subTest(offset=offset):
                esperado = (datetime.now() + timedelta(days=offset)).astimezone(timezone.utc).date()
                self.assertEqual(fecha_iso(offset), esperado.isoformat())



# `time.tzset()` es sólo de Unix: en Windows ni existe. Los tests de abajo
# necesitan fijar la zona horaria para poder comprobar el cruce de día, así
# que ahí se saltean en vez de romper la suite entera.
@unittest.skipUnless(hasattr(time, 'tzset'), 'time.tzset() no existe en esta plataforma')
class FechasConRelojFijo(unittest.TestCase):
    def _con_reloj(self, local: datetime, offset: int) -> str:
        """Corre `fecha_iso` con la hora y la zona horaria fijadas.

        Con el reloj de verdad esto sólo se puede comprobar unas pocas horas al
        día; fijando el reloj, el contrato queda verificado siempre y en
        cualquier máquina (un CI en UTC incluido).
        """
        import importlib
        import os

        import backend.formato as formato

        class RelojFijo(datetime):
            @classmethod
            def now(cls, tz=None):
                return local if tz is None else local.astimezone(tz)

        tz_previa = os.environ.get('TZ')
        os.environ['TZ'] = 'America/Argentina/Buenos_Aires'
        time.tzset()
        original = formato.datetime
        formato.datetime = RelojFijo
        try:
            return formato.fecha_iso(offset)
        finally:
            formato.datetime = original
            if tz_previa is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = tz_previa
            time.tzset()
            importlib.reload(formato)

    def test_a_la_noche_en_argentina_la_fecha_ya_es_la_de_manana(self) -> None:
        # 22:00 en Buenos Aires (UTC-3) son las 01:00 del día siguiente en UTC,
        # y el ISO sale en UTC: la fecha guardada ya es la de mañana.
        self.assertEqual(self._con_reloj(datetime(2026, 9, 21, 22, 0), 0), '2026-09-22')
        self.assertEqual(self._con_reloj(datetime(2026, 9, 21, 22, 0), -1), '2026-09-21')

    def test_de_tarde_la_fecha_sigue_siendo_la_de_hoy(self) -> None:
        # Este es el caso que distingue "mover el día en local" de "partir de la
        # hora UTC": a las 19:00 de Buenos Aires todavía es el 21 en UTC, pero
        # tomando la hora UTC como punto de partida daría el 22.
        self.assertEqual(self._con_reloj(datetime(2026, 9, 21, 19, 0), 0), '2026-09-21')
        self.assertEqual(self._con_reloj(datetime(2026, 9, 21, 19, 0), -1), '2026-09-20')

    def test_al_mediodia_local_y_utc_coinciden(self) -> None:
        self.assertEqual(self._con_reloj(datetime(2026, 9, 21, 12, 0), 0), '2026-09-21')
        self.assertEqual(self._con_reloj(datetime(2026, 9, 21, 12, 0), -120), '2026-05-24')


if __name__ == '__main__':
    unittest.main()
