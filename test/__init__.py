"""Tests del backend.

**Este módulo se importa antes que cualquier otro y fija el entorno**, porque
`backend.config` lee las variables y abre la conexión a SQLite al importarse:
después ya es tarde. Dos cosas que garantiza:

- Los tests NUNCA tocan `data/parva.db`: cada corrida usa una base nueva en un
  directorio temporal.
- Los tests no dependen del `.env` de quien los corre. Como el entorno le gana
  al archivo, fijar las variables acá las deja clavadas aunque el `.env` diga
  otra cosa: sin esto, un `PARSER_MODE=auto` con credenciales de AWS mandaría
  los tests a Bedrock.
"""
import os
import tempfile

DIR_TEMPORAL = tempfile.mkdtemp(prefix='parva-test-')

os.environ.update({
    'DB_PATH': os.path.join(DIR_TEMPORAL, 'parva.db'),
    # Reglas determinísticas: sin modelo, sin red, misma salida siempre.
    'PARSER_MODE': 'mock',
    'CONFIDENCE_THRESHOLD': '0.7',
    # Puerto efímero: el sistema elige uno libre y el test lo lee del socket.
    'PORT': '0',
    # Credenciales de juguete, para firmar y verificar webhooks sin tocar Meta.
    'META_VERIFY_TOKEN': 'token-de-prueba',
    'META_APP_SECRET': 'secreto-de-prueba',
    'META_APP_ID': '',
    'META_ACCESS_TOKEN': '',
    'META_PHONE_NUMBER_ID': '',
    'META_GRAPH_VERSION': 'v25.0',
    'META_REPLY_TO_UNKNOWN': '0',
    # Sin credenciales de AWS ni Ollama: el parser no sale a la red.
    'AWS_ACCESS_KEY_ID': '',
    'AWS_SECRET_ACCESS_KEY': '',
    'AWS_SESSION_TOKEN': '',
    'AWS_REGION': 'us-east-2',
    'BEDROCK_MODEL_ID': 'amazon.nova-lite-v1:0',
    'OLLAMA_URL': 'http://127.0.0.1:1',
})
