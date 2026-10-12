"""Export CSV: la planilla que se baja el productor.

Acá se nota si los números cambian de forma: SQLite devuelve las columnas REAL
como float y, sin `texto_numero`, cada monto pasaría de "1200000" a "1200000.0".
"""
import unittest

from backend.service.export import export_csv

from .utiles import base_limpia, comparar_golden


class ExportCsv(unittest.TestCase):
    def setUp(self) -> None:
        base_limpia()

    def test_las_tres_planillas(self) -> None:
        for sheet in ('movimientos', 'hacienda', 'margenes'):
            with self.subTest(sheet=sheet):
                partes = []
                for pid in (1, 2):
                    nombre, contenido = export_csv(pid, sheet)
                    partes.append(f'--- productor {pid} → {nombre} ---\n{contenido}')
                comparar_golden(self, f'export_{sheet}.csv', '\n'.join(partes) + '\n')

    def test_una_hoja_desconocida_cae_en_movimientos(self) -> None:
        self.assertEqual(export_csv(1, 'cualquier-cosa')[0], 'movimientos.csv')

    def test_los_montos_no_llevan_decimal_de_mas(self) -> None:
        contenido = export_csv(1, 'movimientos')[1]
        self.assertIn('9600000', contenido)
        self.assertNotIn('9600000.0', contenido)

    def test_se_escapan_las_comas_y_comillas(self) -> None:
        from backend.repository import repo
        repo.insert_movimiento(productor_id=1, tipo='gasto', fecha='2026-01-01',
                               producto='algo, con "comillas"', monto=1)
        contenido = export_csv(1, 'movimientos')[1]
        self.assertIn('"algo, con ""comillas"""', contenido)


if __name__ == '__main__':
    unittest.main()
