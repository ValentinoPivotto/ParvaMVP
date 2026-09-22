"""Los scripts de operación: link-phone, tunnel y la regla que comparten.

Son los que se corren a mano cuando algo no anda, así que un error acá manda a
buscar el problema al lugar equivocado. Y uno de ellos abre un túnel público:
que eso pase sólo cuando se lo pide es parte del contrato.
"""
import ast
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.repository import db

from .utiles import RAIZ, base_limpia

#: Los módulos que son puntos de entrada: se ejecutan con `python3 -m`.
PUNTOS_DE_ENTRADA = sorted(
    [*RAIZ.joinpath('backend', 'scripts').glob('*.py'),
     RAIZ / 'backend' / 'repository' / 'seed.py',
     RAIZ / 'backend' / 'handler' / 'server.py'])


def _es_guarda_main(nodo: ast.stmt) -> bool:
    """¿Es un `if __name__ == '__main__':`?"""
    return (isinstance(nodo, ast.If) and isinstance(nodo.test, ast.Compare)
            and isinstance(nodo.test.left, ast.Name) and nodo.test.left.id == '__name__'
            and len(nodo.test.comparators) == 1
            and isinstance(nodo.test.comparators[0], ast.Constant)
            and nodo.test.comparators[0].value == '__main__')


class ImportarNoEjecuta(unittest.TestCase):
    """Importar un script no puede hacer nada más que definir cosas.

    `tunnel.py` no tenía `main()`: su código estaba suelto en el módulo, así
    que cualquier `import` —pydoc, una herramienta del editor, un test—
    abría un túnel ngrok de verdad con el dominio reservado del .env.
    """

    PERMITIDOS = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
                  ast.Assign, ast.AnnAssign)

    def test_ningun_punto_de_entrada_hace_algo_al_importarse(self) -> None:
        self.assertGreaterEqual(len(PUNTOS_DE_ENTRADA), 6)
        for archivo in PUNTOS_DE_ENTRADA:
            arbol = ast.parse(archivo.read_text(encoding='utf-8'))
            for i, nodo in enumerate(arbol.body):
                es_docstring = (i == 0 and isinstance(nodo, ast.Expr)
                                and isinstance(nodo.value, ast.Constant))
                if es_docstring or isinstance(nodo, self.PERMITIDOS) or _es_guarda_main(nodo):
                    continue
                self.fail(f'{archivo.relative_to(RAIZ)}:{nodo.lineno} ejecuta código al '
                          f'importarse ({type(nodo).__name__}). Movelo a main() y llamalo '
                          "desde un `if __name__ == '__main__':`.")


class Tunel(unittest.TestCase):
    """El túnel, con un ngrok falso que anota con qué lo llamaron."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix='parva-ngrok-'))
        self.marca = self.dir / 'llamado'
        falso = self.dir / 'ngrok'
        falso.write_text('#!/bin/sh\necho "$*" > "$NGROK_MARCA"\nexit ${NGROK_FALSO_EXIT:-0}\n')
        falso.chmod(0o755)

    def _correr(self, *codigo_python: str, dominio: str = '', salida: int = 0,
                con_ngrok: bool = True) -> subprocess.CompletedProcess:
        env = {
            'HOME': os.environ.get('HOME', ''),
            'PATH': f'{self.dir}:/usr/bin:/bin' if con_ngrok else '/usr/bin:/bin',
            'PYTHONPATH': str(RAIZ),
            'PORT': '3100',
            # Explícito aunque esté vacío: si no, config lo tomaría del .env real.
            'NGROK_DOMAIN': dominio,
            'NGROK_MARCA': str(self.marca),
            'NGROK_FALSO_EXIT': str(salida),
        }
        return subprocess.run([sys.executable, *codigo_python], env=env, cwd=RAIZ,
                              capture_output=True, text=True, timeout=30)

    def test_importarlo_no_abre_ningun_tunel(self) -> None:
        r = self._correr('-c', 'import backend.scripts.tunnel')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.marca.exists(), 'importar el módulo lanzó ngrok')

    def test_con_dominio_fijo_lo_pasa_sin_el_esquema(self) -> None:
        r = self._correr('-m', 'backend.scripts.tunnel', dominio='https://mi.ngrok-free.app')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.marca.read_text().strip(),
                         'http http://localhost:3100 --url=https://mi.ngrok-free.app')
        self.assertIn('Callback en Meta: https://mi.ngrok-free.app/webhook/whatsapp', r.stdout)

    def test_sin_dominio_avisa_que_la_url_va_a_cambiar(self) -> None:
        r = self._correr('-m', 'backend.scripts.tunnel')
        self.assertEqual(self.marca.read_text().strip(), 'http http://localhost:3100')
        self.assertIn('cambia en CADA restart', r.stdout)

    def test_devuelve_el_codigo_de_salida_de_ngrok(self) -> None:
        self.assertEqual(self._correr('-m', 'backend.scripts.tunnel', salida=3).returncode, 3)

    def test_sin_ngrok_instalado_dice_como_instalarlo(self) -> None:
        r = self._correr('-m', 'backend.scripts.tunnel', con_ngrok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn('brew install ngrok', r.stderr)


class VincularTelefono(unittest.TestCase):
    """`link_phone`: el alta del celular desde el que se escribe al bot."""

    def setUp(self) -> None:
        base_limpia()

    def _correr(self, *args: str) -> tuple[int, str, str]:
        from backend.scripts import link_phone
        salida, errores, codigo = io.StringIO(), io.StringIO(), 0
        argv = sys.argv
        sys.argv = ['link_phone', *args]
        try:
            with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
                link_phone.main()
        except SystemExit as e:
            codigo = e.code or 0
        finally:
            sys.argv = argv
        return codigo, salida.getvalue(), errores.getvalue()

    def _telefono(self, id: int) -> str:
        return db.get('SELECT telefono FROM usuario WHERE id = ?', id)['telefono']

    def test_vincula_y_normaliza_el_telefono(self) -> None:
        # El wa_id argentino puede llegar sin el 9: se guarda la forma canónica.
        codigo, salida, _ = self._correr('1', '541155551234')
        self.assertEqual(codigo, 0)
        self.assertEqual(self._telefono(1), '+5491155551234')
        self.assertIn('+541155551234', salida)    # la variante sin 9 también matchea

    def test_un_id_que_no_existe_no_toca_nada(self) -> None:
        codigo, _, errores = self._correr('99', '+5491155551234')
        self.assertEqual(codigo, 1)
        self.assertIn('No existe el usuario con id 99', errores)

    def test_un_id_no_numerico_se_muestra_tal_como_se_escribio(self) -> None:
        # Antes decía "id None": el valor interno después de intentar convertirlo.
        _, _, errores = self._correr('abc', '+5491155551234')
        self.assertIn('No existe el usuario con id abc', errores)

    def test_un_telefono_de_otro_usuario_no_se_pisa(self) -> None:
        codigo, _, errores = self._correr('1', '+5491100000003')
        self.assertEqual(codigo, 1)
        self.assertIn('ya lo tiene Pedro Díaz', errores)
        self.assertEqual(self._telefono(1), '+5491100000001')

    def test_un_telefono_invalido_se_rechaza(self) -> None:
        codigo, _, errores = self._correr('1', '123')
        self.assertEqual(codigo, 1)
        self.assertIn('Teléfono inválido', errores)

    def test_sin_argumentos_lista_los_usuarios(self) -> None:
        codigo, salida, _ = self._correr()
        self.assertEqual(codigo, 0)
        for nombre in ('Juan Pérez', 'Juan Gómez', 'Pedro Díaz', 'Marta Ruiz'):
            self.assertIn(nombre, salida)


if __name__ == '__main__':
    unittest.main()
