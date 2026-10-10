"""Conversión de valores: números, fechas y JSON, en los formatos que usa Parva.

Están todos juntos acá porque son decisiones de formato que tienen que dar
siempre lo mismo: el monto que ve el productor en WhatsApp, el que sale en el
CSV y el que queda guardado en la base son el mismo número escrito una sola
manera.
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

NAN = float('nan')

# Formas decimales que se aceptan al leer un número de un texto. Python por su
# cuenta también toma '1_000', 'inf' y 'nan', que acá no son números válidos.
_NUMERICO = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$')


def numero(valor: object, por_defecto: object = None) -> float | int:
    """Lee un número de una variable de entorno o de un parámetro de la URL.

    Un valor ausente cae al default; uno vacío cuenta como 0, y uno que no es
    numérico da NaN en vez de tirar. Eso último es a propósito: `bindeable()`
    lo convierte en NULL, un `?productorId=xxx` no matchea ninguna fila y la
    respuesta es un 404 y no un 500.
    """
    if valor is None:
        valor = por_defecto
    if valor is None:
        return NAN
    if isinstance(valor, (int, float)):
        return valor
    s = str(valor).strip()
    if s == '':
        return 0
    if s in ('Infinity', '+Infinity'):
        return math.inf
    if s == '-Infinity':
        return -math.inf
    if not _NUMERICO.match(s):
        return NAN
    n = float(s)
    # Entero cuando el valor lo es, para que SQLite lo guarde como INTEGER.
    return int(n) if n.is_integer() and abs(n) < 2 ** 53 else n


def bindeable(n: float | int | None) -> float | int | None:
    """Prepara un número para bindearlo a SQLite: NaN entra como NULL."""
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return None
    return n


def texto_numero(n: float | int | None) -> str:
    """Escribe un número para que lo lea una persona.

    SQLite devuelve las columnas REAL como float, y `str(1200000.0)` da
    '1200000.0'. Un monto o una cantidad tienen que salir sin decimal de más,
    tanto en el CSV como en los mensajes del bot ("Registré 200 L de gasoil").
    Arriba de 1e21 se pasa a notación exponencial.
    """
    if n is None:
        return ''
    if isinstance(n, bool):
        return 'true' if n else 'false'   # en Python un bool es un int
    if isinstance(n, int):
        return str(n)
    if math.isnan(n):
        return 'NaN'
    if math.isinf(n):
        return 'Infinity' if n > 0 else '-Infinity'
    if n.is_integer() and abs(n) < 1e21:
        return str(int(n))
    return repr(n)


def miles(entero: int) -> str:
    """Separador de miles con punto, como se escriben los números en Argentina."""
    s = f'{abs(entero):,}'.replace(',', '.')
    return '-' + s if entero < 0 else s


def pesos(n: float | int) -> str:
    """Un monto en pesos, redondeado a entero: `$9.600.000`.

    `math.floor(n + 0.5)` y no `round()`: Python redondea al par (`round(2.5)`
    es 2) y acá los .5 van para arriba.
    """
    return '$' + miles(math.floor(n + 0.5))


def fecha_iso(offset_dias: int = 0) -> str:
    """La fecha de hoy (o de hace N días) en formato YYYY-MM-DD.

    Los días se corren sobre el **reloj local** y la fecha se informa en **UTC**.
    En Argentina (UTC-3) eso tiene una consecuencia concreta: a partir de las
    21:00 la fecha que se guarda ya es la del día siguiente.

    No es un descuido: es la convención con la que están fechados todos los
    registros que ya están cargados. Cambiarla los movería de día.
    """
    local = datetime.now() + timedelta(days=offset_dias)
    return local.astimezone(timezone.utc).date().isoformat()


def texto_o_undefined(v: object) -> str:
    """Un valor para meter en un mensaje; si falta, la palabra "undefined".

    ⚠ El nombre es feo porque el comportamiento lo es. Se llega acá por el
    camino de confirmación: el productor responde "sí" a un pendiente al que le
    falta un campo requerido, el registro se guarda igual y el bot contesta
    "✅ Registré undefined.".

    Es un defecto conocido y está acá aislado, en vez de escondido detrás de
    cuatro f-strings. Arreglarlo cambia lo que el bot contesta, así que es una
    decisión aparte: o no se persiste un pendiente incompleto, o el mensaje se
    redacta sin ese campo.
    """
    if v is None:
        return 'undefined'
    if isinstance(v, (int, float)):
        return texto_numero(v)
    return str(v)


def _numeros_json(v: object) -> object:
    """Deja los números de una estructura listos para serializar."""
    if isinstance(v, bool):
        return v                      # antes que int: en Python un bool es int
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None               # NaN no es JSON válido: rompería el parseo del frontend
        if v.is_integer() and abs(v) < 2 ** 53:
            return int(v)
        return v
    if isinstance(v, dict):
        return {k: _numeros_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_numeros_json(x) for x in v]
    return v


def a_json(obj: object) -> str:
    """Serializa a JSON en el formato que usa la app.

    Tres detalles, los tres visibles en la salida: compacto (sin espacios
    después de ',' y ':'), sin escapar los no-ASCII (los acentos viajan
    literales) y con los flotantes enteros escritos como enteros — un monto que
    SQLite devuelve como 1200000.0 se escribe '1200000'.

    Importa para `raw_message.parsed_json`, que se vuelve a leer tal cual, y
    para el estado que consume el frontend.
    """
    return json.dumps(_numeros_json(obj), ensure_ascii=False, separators=(',', ':'))


# Caracteres que no hace falta escapar dentro de una URL, además de letras y
# dígitos: son seguros tanto en un path como en un query string.
_SEGUROS_URI = "-_.!~*'()"


def percent_encode(s: str) -> str:
    """Percent-encoding para meter un valor adentro de una URL.

    `quote` de Python deja pasar '/' por defecto, y acá hay que escaparlo: un id
    de modelo de Bedrock como `amazon.nova-lite-v1:0` tiene que viajar entero
    como un solo segmento del path.
    """
    return quote(s, safe=_SEGUROS_URI)
