"""El prompt que se le manda al modelo (Bedrock y Ollama comparten uno solo).

Editarlo sin querer no rompe ningún test ni tira ningún error: simplemente el
modelo empieza a parsear peor, y eso se nota semanas después mirando registros
mal cargados. Por eso queda congelado.
"""
import json
import unittest

from backend.service.parser import _construir_mensajes

from .utiles import comparar_golden


class Prompt(unittest.TestCase):
    def test_mensajes_completos(self) -> None:
        msgs = _construir_mensajes('Compré 200 litros de gasoil para el lote 4')
        comparar_golden(self, 'prompt.json', json.dumps(msgs, indent=1, ensure_ascii=False) + '\n')

    def test_la_fecha_de_hoy_va_inyectada(self) -> None:
        # El modelo no tiene forma de saber qué día es, y sin eso no puede
        # resolver "ayer": el registro terminaba fechado hoy y nadie se enteraba.
        from backend.formato import fecha_iso
        sistema = _construir_mensajes('hola')[0]['content']
        self.assertIn(f'Hoy es {fecha_iso(0)}', sistema)
        self.assertIn(f'"ayer" es el día anterior a {fecha_iso(0)}', sistema)

    def test_los_ejemplos_van_fechados_coherentes(self) -> None:
        # Un ejemplo con fecha que contradiga la regla que el modelo acaba de
        # leer es peor que no darlo.
        from backend.formato import fecha_iso
        msgs = _construir_mensajes('hola')
        ejemplo_ayer = next(m for m in msgs if m['role'] == 'assistant' and 'novillo' in m['content'])
        self.assertIn(fecha_iso(-1), ejemplo_ayer['content'])


if __name__ == '__main__':
    unittest.main()
