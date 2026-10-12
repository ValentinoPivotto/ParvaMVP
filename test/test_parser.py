"""Reglas determinísticas del parser, sobre un corpus de mensajes reales.

Es el piso del bot: sin credenciales de AWS ni Ollama, todo mensaje se parsea
acá. Las reglas son un montón de regex que interactúan entre sí, así que la
forma de cuidarlas es congelar la salida sobre un corpus y mirar el diff.

`test/corpus.json` incluye a propósito los bordes que costaron: acentos
("qué" vs "que", "aré" vs "are"), confirmaciones que no deben serlo ("sino",
"sillón"), miles con punto y decimales con coma, y mensajes vacíos.
"""
import json
import unittest

from backend.formato import a_json
from backend.service.parser import parse_mock

from .utiles import CORPUS, comparar_golden


def _serializar(p) -> str:
    """Una línea por mensaje, con las claves en orden fijo.

    Se serializa con `a_json` (el mismo de producción) para que una cantidad
    entera salga "200" y no "200.0".
    """
    campos = {k: v for k, v in p.fields.items() if v is not None}
    query = {k: v for k, v in p.query.items() if v is not None} if p.query else None
    return a_json({
        'mensaje': p.raw_text,
        'intent': p.intent,
        'recordType': p.record_type,
        'fields': campos,
        'query': query,
        'confidence': p.confidence,
    })


class ReglasDelParser(unittest.TestCase):
    def test_corpus_completo(self) -> None:
        casos = json.loads(CORPUS.read_text(encoding='utf-8'))
        salida = '\n'.join(_serializar(parse_mock(c)) for c in casos) + '\n'
        comparar_golden(self, 'parser.jsonl', salida)

    def test_el_corpus_cubre_los_bordes_que_importan(self) -> None:
        """Si alguien poda el corpus, que se entere de qué está sacando."""
        casos = json.loads(CORPUS.read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(casos), 100)
        for imprescindible in ('sí', 'sino', 'qué tengo', 'que tengo', 'aré el lote 4',
                               'are el lote 4', 'vendí 2,5 tn de trigo',
                               'ayer vendí 30 novillos a 1.200.000 en total'):
            self.assertIn(imprescindible, casos)

    def test_los_acentos_no_cambian_el_borde_de_palabra(self) -> None:
        """Las regex van en modo ASCII, y eso cambia qué mensajes son preguntas.

        En `qu[eé]\\b` la variante con tilde nunca llega a matchear: después de
        'é' no hay borde de palabra en ASCII. En modo Unicode sí matchearía, y
        el efecto no es cosmético: "qué vendí" pasaría de registrar una venta a
        contestar cuánto se vendió.
        """
        self.assertEqual(parse_mock('que vendí').intent, 'query')
        self.assertEqual(parse_mock('qué vendí').record_type, 'venta')
        self.assertEqual(parse_mock('que gasté').intent, 'query')
        self.assertEqual(parse_mock('qué gasté').record_type, 'gasto')
        # Lo mismo en las labores, con `ar[ée]\\b`.
        self.assertEqual(parse_mock('are el lote 4').record_type, 'labor')
        self.assertEqual(parse_mock('aré el lote 4').intent, 'unknown')


if __name__ == '__main__':
    unittest.main()
