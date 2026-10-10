"""Levanta el túnel público que Meta necesita para entregar los webhooks.

  python3 -m backend.scripts.tunnel

Con NGROK_DOMAIN el dominio es FIJO: la callback URL se registra en Meta una
sola vez y sobrevive a los restarts. Sin él, cada arranque da una URL nueva y
hay que re-registrarla a mano — la trampa que deja al bot mudo sin un solo
error visible, porque Meta sigue entregando los webhooks a la URL vieja.
"""
import os
import re
import subprocess
import sys

from ..config import config

# El puerto sale del .env porque config lo carga al importarse. Antes el script
# era shell puro (`${PORT:-3000}`) y leía el PORT del SHELL, que nunca ve el
# .env: si los dos diferían, el túnel apuntaba al puerto equivocado y no lo decía.
destino = f'http://localhost:{config.port}'
dominio = re.sub(r'^https?://', '', (os.environ.get('NGROK_DOMAIN') or '').strip())

args = ['http', destino]
if dominio:
    args.append(f'--url=https://{dominio}')

print(f'\n🔌 Túnel ngrok → {destino}')
if dominio:
    print(f'   Callback en Meta: https://{dominio}/webhook/whatsapp')
    print('   Dominio fijo: ya está registrada, no hay que re-pegarla.\n')
else:
    print('   ⚠ Sin NGROK_DOMAIN la URL cambia en CADA restart y hay que volver')
    print('     a registrarla en Meta. Reservá un dominio gratis en')
    print('     https://dashboard.ngrok.com/domains y cargalo en .env.\n')

try:
    # stdio heredado: la salida de ngrok va derecho a la terminal.
    codigo = subprocess.run(['ngrok', *args]).returncode
except FileNotFoundError:
    print('✗ ngrok no está instalado.  brew install ngrok', file=sys.stderr)
    print('  Después: ngrok config add-authtoken <tu token>  (dashboard.ngrok.com)', file=sys.stderr)
    sys.exit(1)
except OSError as err:
    print(f'✗ no se pudo levantar ngrok: {err}', file=sys.stderr)
    sys.exit(1)
except KeyboardInterrupt:
    codigo = 0

sys.exit(codigo)
