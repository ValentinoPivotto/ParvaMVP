"""Datos de ejemplo.

La semilla es lo que ve cualquiera que clona el repo y arranca, y es la base de
casi todos los demás tests. Los dos productores están elegidos para ejercitar
las dos variantes: uno agrícola con márgenes por lote (uno confiable y otro no)
y uno ganadero con hacienda, eventos y sanidad.
"""
import unittest

from backend.repository import db

from .utiles import base_limpia, comparar_golden, volcar_tablas

TABLAS = ['productor', 'usuario', 'campo', 'lote', 'campania', 'movimiento',
          'hacienda', 'evento_hacienda', 'evento_sanitario', 'audit_log']


class Semilla(unittest.TestCase):
    def setUp(self) -> None:
        base_limpia()

    def test_contenido_completo(self) -> None:
        comparar_golden(self, 'seed.txt', volcar_tablas(TABLAS))

    def test_el_esquema_no_cambia(self) -> None:
        filas = db.all("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")
        comparar_golden(self, 'esquema.sql', '\n'.join(f['sql'] + ';' for f in filas) + '\n')

    def test_el_indice_unico_del_dedup_existe(self) -> None:
        # Es lo que hace el dedup de los reintentos de Meta a prueba de carreras.
        # Admite infinitos NULL: los mensajes sin id de Meta conviven sin problema.
        indices = [f['name'] for f in db.all("PRAGMA index_list('raw_message')")]
        self.assertIn('ux_raw_message_wa_id', indices)

    def test_sembrar_dos_veces_no_duplica(self) -> None:
        # `seed_if_empty` sólo siembra con la base vacía; un segundo arranque
        # tiene que dejar los mismos dos productores.
        from backend.repository.seed import seed_if_empty
        seed_if_empty()
        self.assertEqual(len(db.all('SELECT id FROM productor')), 2)


if __name__ == '__main__':
    unittest.main()
