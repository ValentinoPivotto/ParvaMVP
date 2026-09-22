"""Semántica de JavaScript que Python no comparte.

La migración desde TypeScript tenía que ser transparente: los mismos textos en
WhatsApp, los mismos CSV y las mismas fechas que producía la versión anterior.
Estos son los únicos puntos donde los dos lenguajes no coinciden solos, así que
la diferencia vive acá concentrada y no desparramada por el resto del código.
"""
import json
import math
import re
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

NAN = float('nan')

# Lo que `Number(string)` acepta en decimal. Python es más permisivo (toma
# '1_000', 'inf', 'nan'), y esas formas en JS daban NaN.
_NUMERICO = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$')


def numero(valor: object, por_defecto: object = None) -> float | int:
    """`Number(x ?? por_defecto)` de JS.

    Devuelve NaN en vez de tirar, porque el código que la usa cuenta con eso: un
    `?productorId=` basura terminaba en un SELECT que no matcheaba nada y
    respondía 404, no en un 500.
    """
    if valor is None:
        valor = por_defecto
    if valor is None:
        return NAN
    if isinstance(valor, (int, float)):
        return valor
    s = str(valor).strip()
    if s == '':
        return 0          # Number('') === 0
    if s in ('Infinity', '+Infinity'):
        return math.inf
    if s == '-Infinity':
        return -math.inf
    if not _NUMERICO.match(s):
        return NAN
    n = float(s)
    # JS tiene un solo tipo numérico. Devolver int cuando el valor es entero
    # hace que SQLite lo bindee como INTEGER, igual que node:sqlite con un
    # número entero de JS.
    return int(n) if n.is_integer() and abs(n) < 2 ** 53 else n


def bindeable(n: float | int | None) -> float | int | None:
    """NaN → None, para bindear como hacía node:sqlite (NaN entraba como NULL)."""
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return None
    return n


def texto_numero(n: float | int | None) -> str:
    """`String(n)` de JS.

    SQLite devuelve las columnas REAL como float, y `str(1200000.0)` en Python
    da '1200000.0' donde JS daba '1200000'. Sin esto el CSV exportado cambiaba
    en cada monto. (Arriba de 1e21 los dos pasan a notación exponencial.)
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
    """`entero.toLocaleString('es-AR')`: separador de miles con punto."""
    s = f'{abs(entero):,}'.replace(',', '.')
    return '-' + s if entero < 0 else s


def pesos(n: float | int) -> str:
    """`'$' + Math.round(n).toLocaleString('es-AR')`.

    `math.floor(n + 0.5)` y no `round()`: Python redondea al par (round(2.5) es
    2) y `Math.round` de JS manda los .5 para arriba.
    """
    return '$' + miles(math.floor(n + 0.5))


def fecha_iso(offset_dias: int = 0) -> str:
    """`d.setDate(d.getDate() + off); d.toISOString().slice(0, 10)`.

    El detalle importante: JS mueve los días en hora LOCAL y recién después pasa
    a UTC para el ISO. En Argentina (UTC-3) eso significa que a partir de las
    21:00 la fecha guardada ya es la de mañana. Los datos cargados hasta hoy
    salieron de ese comportamiento, así que se replica en vez de "arreglarlo":
    cambiarlo movería de día registros que ya están en la base.
    """
    local = datetime.now() + timedelta(days=offset_dias)
    return local.astimezone(timezone.utc).date().isoformat()


def texto_js(v: object) -> str:
    """Lo que imprime un `${v}` de JavaScript.

    Hace falta donde el valor puede faltar: por el camino de confirmación se
    puede llegar a persistir un registro sin producto (el productor responde
    "sí" a un pendiente incompleto) y el bot contestaba "Registré undefined".
    Es feo, pero es lo que hace hoy: replicarlo mantiene la migración invisible
    en vez de cambiar el texto por "None".
    """
    if v is None:
        return 'undefined'
    if isinstance(v, (int, float)):
        return texto_numero(v)
    return str(v)


def _numeros_js(v: object) -> object:
    """Normaliza los números de una estructura a como los vería JavaScript."""
    if isinstance(v, bool):
        return v                      # antes que int: en Python un bool es int
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None               # `JSON.stringify(NaN)` da null
        if v.is_integer() and abs(v) < 2 ** 53:
            return int(v)
        return v
    if isinstance(v, dict):
        return {k: _numeros_js(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_numeros_js(x) for x in v]
    return v


def a_json(obj: object) -> str:
    """`JSON.stringify(obj)`.

    Tres diferencias con el `json.dumps` pelado, las tres visibles en la salida:
    sin espacios después de ',' y ':', sin escapar los no-ASCII (los acentos
    viajan literales), y los flotantes enteros como enteros — JS tiene un solo
    tipo numérico, así que un monto que SQLite devuelve como 1200000.0 se
    serializaba '1200000'.
    """
    return json.dumps(_numeros_js(obj), ensure_ascii=False, separators=(',', ':'))


# Lo que `encodeURIComponent` NO escapa, además de letras y dígitos.
_SEGURO_URI = "-_.!~*'()"


def encode_uri_component(s: str) -> str:
    """`encodeURIComponent(s)`: `quote` de Python deja pasar '/' y estos seis."""
    return quote(s, safe=_SEGURO_URI)
