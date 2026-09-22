"""Backend de Parva."""
import sys

# Node escribe los logs apenas se producen aunque la salida esté redirigida;
# Python los acumula en bloques de 8 KB cuando stdout no es una terminal. Con
# `parva > parva.log` (o corriendo bajo un supervisor) el arranque y los
# `[wa] ←` salían al instante: sin esto no se vería nada hasta llenar el buffer.
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, 'reconfigure'):
        _flujo.reconfigure(line_buffering=True)
