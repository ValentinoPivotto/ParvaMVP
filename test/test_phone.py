"""Normalización de teléfonos.

El wa_id que manda Meta para móviles argentinos a veces trae el 9 y a veces no,
y de eso depende que el remitente matchee con su usuario: si no matchea, el bot
lo ignora en silencio.
"""
import unittest

from backend.phone import normalize_telefono, phone_variants, solo_digitos, to_wa_id

#  entrada            dígitos          canónico          variantes                                        wa_id
CASOS = [
    ('+5491100000001', '5491100000001', '+5491100000001',
     ['+5491100000001', '+541100000001'], '541100000001'),
    ('541112345678', '541112345678', '+5491112345678',
     ['541112345678', '+541112345678', '+5491112345678'], '541112345678'),
    ('5491112345678', '5491112345678', '+5491112345678',
     ['5491112345678', '+5491112345678', '+541112345678'], '541112345678'),
    ('00541112345678', '541112345678', '+5491112345678',
     ['00541112345678', '+541112345678', '+5491112345678'], '541112345678'),
    ('+54 9 11 1234-5678', '5491112345678', '+5491112345678',
     ['+54 9 11 1234-5678', '+5491112345678', '+541112345678'], '541112345678'),
    ('', '', '', [], ''),
    # Un número que no es AR móvil no se toca.
    ('+12025550123', '12025550123', '+12025550123', ['+12025550123'], '12025550123'),
]


class Telefonos(unittest.TestCase):
    def test_solo_digitos(self) -> None:
        for entrada, digitos, _, _, _ in CASOS:
            with self.subTest(entrada=entrada):
                self.assertEqual(solo_digitos(entrada), digitos)

    def test_forma_canonica(self) -> None:
        for entrada, _, canonico, _, _ in CASOS:
            with self.subTest(entrada=entrada):
                self.assertEqual(normalize_telefono(entrada), canonico)

    def test_variantes_en_orden_y_sin_repetir(self) -> None:
        # El orden importa: un teléfono ya canónico tiene que matchear en la
        # primera consulta, sin pasar por las variantes.
        for entrada, _, _, variantes, _ in CASOS:
            with self.subTest(entrada=entrada):
                self.assertEqual(phone_variants(entrada), variantes)
                self.assertEqual(len(variantes), len(set(variantes)))

    def test_wa_id_para_enviar(self) -> None:
        # A un móvil AR hay que sacarle el 9 o la Cloud API tira 131030.
        for entrada, _, _, _, wa_id in CASOS:
            with self.subTest(entrada=entrada):
                self.assertEqual(to_wa_id(entrada), wa_id)


if __name__ == '__main__':
    unittest.main()
