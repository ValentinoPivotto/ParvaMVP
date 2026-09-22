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


class RegistrosIncompletos(unittest.TestCase):
    """Qué pasa cuando el productor confirma algo a lo que le falta un dato.

    El bot pregunta "¿lo registro igual?" y el productor dice que sí. El
    registro entra —lo pidió— pero el recibo no puede afirmar lo que no
    guardó: ni un "$0" que nadie cargó ni un producto que nadie nombró.
    """

    def setUp(self) -> None:
        base_limpia()
        self.sender = get_sender_by_telefono('+5491100000001')

    def _confirmar(self, mensaje: str, marca: str) -> str:
        pide = process_message(self.sender, mensaje, f'wamid.{marca}.a')
        self.assertEqual(pide.status, 'needs_confirmation', f'«{mensaje}» no pidió confirmación')
        return process_message(self.sender, 'sí', f'wamid.{marca}.b').reply

    def test_un_insumo_sin_producto_no_inventa_un_nombre(self) -> None:
        reply = self._confirmar('compré', 'inc1')
        self.assertEqual(reply, '✅ Registré un insumo. No me dijiste qué producto.')

    def test_un_gasto_sin_monto_no_inventa_un_cero(self) -> None:
        # En la base el monto queda NULL: anunciar "$0" es afirmar un importe
        # que nadie cargó, y encima queda en la planilla como si fuera real.
        reply = self._confirmar('gasté', 'inc2')
        self.assertNotIn('$0', reply)
        self.assertEqual(reply, '✅ Registré un gasto. No me dijiste el monto.')
        self.assertIsNone(db.get("SELECT monto FROM movimiento WHERE origen='bot'")['monto'])

    def test_una_venta_sin_monto_tampoco(self) -> None:
        reply = self._confirmar('vendí', 'inc3')
        self.assertNotIn('$0', reply)
        self.assertEqual(reply, '✅ Registré una venta. No me dijiste el monto.')

    def test_un_lote_que_no_existe_no_se_afirma_en_el_recibo(self) -> None:
        # El movimiento quedó sin lote: decir "en el lote 99" sería mentir.
        reply = self._confirmar('compré gasoil para el lote 99', 'inc4')
        self.assertNotIn('en el lote 99', reply)
        self.assertIn('No lo pude asociar al lote 99', reply)
        self.assertIsNone(db.get("SELECT lote_id FROM movimiento WHERE origen='bot'")['lote_id'])

    def test_un_lote_que_si_existe_se_nombra(self) -> None:
        r = process_message(self.sender, 'compré 200 litros de gasoil para el lote 4', 'wamid.inc5')
        self.assertIn('en el lote 4', r.reply)
        self.assertIsNotNone(db.get("SELECT lote_id FROM movimiento WHERE origen='bot'")['lote_id'])


class DatosImposibles(unittest.TestCase):
    """Cuando falta un dato sin el cual el registro no puede entrar.

    `evento_hacienda.categoria` y `.cantidad` son NOT NULL. Ofrecer "¿lo
    registro igual?" ahí es prometer algo que no va a pasar: el INSERT falla,
    el error se queda en el log y el productor no recibe nada.
    """

    def setUp(self) -> None:
        base_limpia()
        self.sender = get_sender_by_telefono('+5491100000001')

    def test_no_ofrece_registrar_lo_que_no_puede_guardar(self) -> None:
        r = process_message(self.sender, 'nacieron terneros', 'wamid.imp.1')
        self.assertEqual(r.status, 'needs_data')
        self.assertNotIn('¿Lo registro igual?', r.reply)
        self.assertIn('Repetímelo con ese dato', r.reply)
        # Y da un ejemplo de la forma que sí entiende.
        self.assertIn('nacieron 8 terneros', r.reply)

    def test_no_deja_un_pendiente_colgado(self) -> None:
        process_message(self.sender, 'nacieron terneros', 'wamid.imp.2')
        self.assertEqual(db.all("SELECT id FROM raw_message WHERE estado = 'pending'"), [])

    def test_el_si_no_queda_trabado_en_un_loop(self) -> None:
        # Antes: el INSERT fallaba, el pendiente quedaba abierto y cada "sí"
        # siguiente volvía a fallar. Para siempre, y sin decir nada.
        process_message(self.sender, 'nacieron terneros', 'wamid.imp.3')
        for i in range(3):
            r = process_message(self.sender, 'sí', f'wamid.imp.3.si{i}')
            self.assertIsNotNone(r, 'el "sí" tiene que contestar algo')
            self.assertEqual(r.reply, 'No tengo nada pendiente para confirmar.')

    def test_si_persistir_falla_el_pendiente_se_cierra_igual(self) -> None:
        """Cinturón de seguridad, por si algún día vuelve a fallar un INSERT.

        Con el arreglo de arriba este camino ya no se alcanza desde el flujo
        normal, pero si un pendiente no se pudiera guardar, dejarlo abierto
        haría que cada "sí" posterior reintente lo mismo y falle igual.
        """
        from backend.service import process as modulo

        # Un pendiente cualquiera, de los que sí se pueden guardar.
        process_message(self.sender, 'compré gasoil', 'wamid.cint.1')
        self.assertEqual(len(db.all("SELECT id FROM raw_message WHERE estado='pending'")), 1)

        original = modulo._persistir
        modulo._persistir = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('falla simulada'))
        try:
            with self.assertRaises(RuntimeError):
                process_message(self.sender, 'sí', 'wamid.cint.2')
        finally:
            modulo._persistir = original

        self.assertEqual(db.all("SELECT id FROM raw_message WHERE estado='pending'"), [],
                         'el pendiente tendría que haberse cerrado')
        # Y el siguiente "sí" contesta en vez de volver a explotar.
        self.assertEqual(process_message(self.sender, 'sí', 'wamid.cint.3').reply,
                         'No tengo nada pendiente para confirmar.')

    def test_el_mensaje_correcto_sigue_entrando_derecho(self) -> None:
        r = process_message(self.sender, 'nacieron 8 terneros', 'wamid.imp.4')
        self.assertEqual(r.status, 'created')
        self.assertEqual(r.reply, '✅ Registré nacimiento de 8 ternero. Stock actualizado.')


if __name__ == '__main__':
    unittest.main()
