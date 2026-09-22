"""El recorrido completo de un mensaje, punta a punta.

`mensaje → transcribe → parse → normalize → validate → persist`, con permisos
por rol, confirmaciones y aislamiento por tenant. Es el test más valioso del
repo: cubre de una todas las capas y congela dos cosas a la vez — **lo que el
bot le contesta al productor** y **lo que queda guardado en la base**.

Se corre el corpus entero con dos remitentes elegidos a propósito:
  · Juan Pérez  (owner del agrícola)  — puede todo
  · Marta Ruiz  (gestor_campo del ganadero) — no puede vender ni ver márgenes
"""
import unittest

from backend.formato import a_json
from backend.repository import db
from backend.repository.repo import get_sender_by_telefono
from backend.service.process import process_message

from .utiles import base_limpia, comparar_golden, volcar_tablas

REMITENTES = ['+5491100000001', '+5491100000004']
TABLAS = ['movimiento', 'hacienda', 'evento_hacienda', 'evento_sanitario', 'audit_log']


def _correr_corpus() -> list[str]:
    import json

    from .utiles import CORPUS
    casos = json.loads(CORPUS.read_text(encoding='utf-8'))
    lineas: list[str] = []
    for tel in REMITENTES:
        sender = get_sender_by_telefono(tel)
        assert sender is not None, f'el remitente {tel} tiene que estar sembrado'
        for i, mensaje in enumerate(casos):
            r = process_message(sender, mensaje, f'wamid.{tel}.{i}')
            respuesta = 'None (duplicado)' if r is None else f'[{r.status}] {r.reply}'
            lineas.append(f'{tel} · {a_json(mensaje)}\n    → {respuesta}')
    return lineas


class Pipeline(unittest.TestCase):
    def setUp(self) -> None:
        base_limpia()

    def test_respuestas_del_bot(self) -> None:
        comparar_golden(self, 'pipeline_respuestas.txt', '\n'.join(_correr_corpus()) + '\n')

    def test_estado_de_la_base_despues_del_corpus(self) -> None:
        _correr_corpus()
        comparar_golden(self, 'pipeline_base.txt', volcar_tablas(TABLAS))

    def test_el_formato_de_parsed_json_es_estable(self) -> None:
        """Las claves y su orden importan: `parsed_json` se vuelve a leer para
        confirmar un pendiente, y una base con filas viejas y nuevas no puede
        terminar con dos formatos conviviendo."""
        sender = get_sender_by_telefono('+5491100000001')
        process_message(sender, 'compré gasoil', 'wamid.formato.1')
        fila = db.get("SELECT parsed_json FROM raw_message WHERE wa_message_id = 'wamid.formato.1'")
        self.assertEqual(
            fila['parsed_json'],
            '{"recordType":"insumo","loteId":null,"loteResuelto":true,'
            '"producto":"gasoil","fecha":"%s"}' % __import__('backend.formato', fromlist=['x']).fecha_iso(0))

    def test_los_reintentos_de_meta_se_dedupean(self) -> None:
        # Meta entrega at-least-once: sin el índice único, cada reintento
        # duplicaría el registro y el stock se movería dos veces.
        sender = get_sender_by_telefono('+5491100000001')
        primero = process_message(sender, 'Nacieron 3 terneros', 'wamid.repetido')
        segundo = process_message(sender, 'Nacieron 3 terneros', 'wamid.repetido')
        self.assertIsNotNone(primero)
        self.assertIsNone(segundo, 'el reintento tendría que ignorarse')
        self.assertEqual(len(db.all("SELECT id FROM evento_hacienda WHERE origen = 'bot'")), 1)

    def test_un_mensaje_sin_id_no_se_dedupea_contra_otro(self) -> None:
        # El envelope de un curl de prueba no trae msg.id. Se pasa None (no '')
        # justamente para que el índice único no los tome por el mismo mensaje.
        sender = get_sender_by_telefono('+5491100000001')
        self.assertIsNotNone(process_message(sender, 'Nacieron 1 terneros', None))
        self.assertIsNotNone(process_message(sender, 'Nacieron 1 terneros', None))

    def test_confirmar_un_pendiente_lo_registra(self) -> None:
        sender = get_sender_by_telefono('+5491100000001')
        # "compré gasoil" no trae cantidad ni monto: el bot pide confirmación.
        pide = process_message(sender, 'compré gasoil', 'wamid.conf.1')
        self.assertEqual(pide.status, 'needs_confirmation')
        self.assertEqual(len(db.all("SELECT id FROM movimiento WHERE origen = 'bot'")), 0)
        ok = process_message(sender, 'sí', 'wamid.conf.2')
        self.assertEqual(ok.status, 'confirmed')
        self.assertEqual(len(db.all("SELECT id FROM movimiento WHERE origen = 'bot'")), 1)

    def test_el_umbral_de_confianza_pide_confirmacion(self) -> None:
        """Un mensaje con todos los campos requeridos pero baja confianza tiene
        que pedir confirmación igual: es el único guardrail que queda cuando el
        parser entendió algo plausible pero no está seguro.

        "compré gasoil por $5.000" trae producto y monto (pasa los campos
        requeridos) pero sin cantidad la confianza es 0.62, debajo del 0.7.
        """
        sender = get_sender_by_telefono('+5491100000001')
        dudoso = process_message(sender, 'compré gasoil por $5.000', 'wamid.umbral.1')
        self.assertEqual(dudoso.status, 'needs_confirmation')
        self.assertLess(dudoso.confidence, 0.7)
        self.assertEqual(len(db.all("SELECT id FROM movimiento WHERE origen = 'bot'")), 0)

        # El mismo tipo de registro, con confianza por encima del umbral, entra
        # derecho: así se ve que lo que decidió fue la confianza y no otra regla.
        seguro = process_message(sender, 'compré 3 bolsas de semilla', 'wamid.umbral.2')
        self.assertEqual(seguro.status, 'created')
        self.assertGreaterEqual(seguro.confidence, 0.7)
        self.assertEqual(len(db.all("SELECT id FROM movimiento WHERE origen = 'bot'")), 1)

    def test_el_gestor_de_campo_no_puede_vender_ni_ver_margenes(self) -> None:
        gestor = get_sender_by_telefono('+5491100000004')
        venta = process_message(gestor, 'Vendí 240 tn de soja por $9.600.000', 'wamid.perm.1')
        self.assertEqual(venta.status, 'denied')
        margen = process_message(gestor, '¿cuál es el margen?', 'wamid.perm.2')
        self.assertIn('no puede consultar margen', margen.reply)
        self.assertEqual(len(db.all("SELECT id FROM movimiento WHERE origen = 'bot'")), 0)

    def test_cada_productor_solo_ve_lo_suyo(self) -> None:
        """El guardrail principal: todo va filtrado por productor_id."""
        agricola = get_sender_by_telefono('+5491100000001')
        ganadero = get_sender_by_telefono('+5491100000003')
        self.assertNotEqual(agricola.productor_id, ganadero.productor_id)
        process_message(agricola, 'Nacieron 7 terneros', 'wamid.tenant.1')
        stock_ganadero = process_message(ganadero, '¿cuántos terneros tengo?', 'wamid.tenant.2')
        # El ganadero arranca con 45 y no tiene que ver los 7 del vecino.
        self.assertIn('45 ternero', stock_ganadero.reply)


if __name__ == '__main__':
    unittest.main()
