"""Servidor con recarga al editar.

  python3 -m backend.scripts.dev

Python no trae un watcher en la stdlib, así que se mira el mtime de los .py
del backend y se reinicia el proceso cuando alguno cambia. Sondear cada
300 ms alcanza de sobra para editar y recargar, y evita traer una
dependencia sólo para desarrollo.
"""
import os
import signal
import subprocess
import sys
import time

from ..config import RAIZ

INTERVALO_S = 0.3
VIGILADO = RAIZ / 'backend'


def _huella() -> dict[str, float]:
    """mtime de cada .py del backend. Un archivo nuevo o borrado también cuenta."""
    return {str(p): p.stat().st_mtime for p in VIGILADO.rglob('*.py') if p.is_file()}


def main() -> None:
    cmd = [sys.executable, '-m', 'backend.handler.server']
    entorno = {**os.environ, 'PYTHONPATH': str(RAIZ)}
    proc = subprocess.Popen(cmd, cwd=RAIZ, env=entorno)
    anterior = _huella()
    print(f'👀 watch: {VIGILADO.relative_to(RAIZ)}/**/*.py — se reinicia solo al guardar\n')

    try:
        while True:
            time.sleep(INTERVALO_S)
            if proc.poll() is not None:
                # El server se cayó (un error de sintaxis, por ejemplo): se espera
                # al próximo guardado en vez de reintentar en loop.
                nueva = _huella()
                if nueva == anterior:
                    continue
                anterior = nueva
                print('\n🔄 cambio detectado — reiniciando\n')
                proc = subprocess.Popen(cmd, cwd=RAIZ, env=entorno)
                continue
            nueva = _huella()
            if nueva != anterior:
                anterior = nueva
                print('\n🔄 cambio detectado — reiniciando\n')
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                proc = subprocess.Popen(cmd, cwd=RAIZ, env=entorno)
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == '__main__':
    main()
