"""Backend de Parva."""
import sys

# Python acumula stdout en bloques de 8 KB cuando la salida no es una terminal.
# Corriendo con `> parva.log` o bajo un supervisor, el arranque y los `[wa] ←`
# no aparecerían hasta llenar el buffer, que es justo cuando hacen falta.
# Con line buffering cada línea sale apenas se escribe.
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, 'reconfigure'):
        _flujo.reconfigure(line_buffering=True)
