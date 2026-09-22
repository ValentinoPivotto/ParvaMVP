"""Parser: convierte el mensaje informal en un ParsedIntent estructurado.

Por default usa un MOCK determinístico (reglas en español). Con credenciales
reales usa Nova Lite sobre Bedrock o un modelo local vía Ollama, los dos con
el mismo shape de salida.
"""
import json
import math
import re
import sys
import threading
from typing import Any

from ..config import config, use_bedrock
from ..formato import ESPACIOS, NAN, a_json, fecha_iso, numero, percent_encode, recortar, texto_numero
from ..red import pedir
from ..types import ParsedIntent
from .sigv4 import AwsCreds, firmar_aws

# Un modelo colgado no puede dejar esperando al webhook: Meta reintenta y el
# productor se queda sin respuesta. Cortamos y caemos al mock, que contesta
# siempre. Mismo criterio que el timeout de whatsapp.py.
TIMEOUT_MODELO_S = 10.0

# Las regex van en modo ASCII: `\b`, `\d` y `\w` no toman los acentos como
# parte de una palabra. No es cosmético. En `qu[eé]\b` la variante con tilde
# nunca llega a matchear, porque después de 'é' no hay borde de palabra; en
# modo Unicode sí matchearía y "qué vendí" pasaría de registrar una venta a
# contestar cuánto se vendió.
_ASCII = re.ASCII
_ASCII_I = re.ASCII | re.IGNORECASE

# `re.ASCII` también achica `\s` a los cinco espacios de siempre, y los mensajes
# llegan de teclados de celular: el espacio no separable U+00A0 aparece solo al
# escribir "lote 4" en iOS. Con `\s` en modo ASCII ese mensaje se guardaba sin
# lote y sin pedir confirmación, así que la clase va escrita a mano con todos
# los separadores Unicode.
_ESP = '[' + ''.join(re.escape(c) for c in ESPACIOS) + ']'

CATEGORIAS_ANIMAL: dict[str, str] = {
    'ternero': 'ternero', 'terneros': 'ternero', 'ternera': 'ternero', 'terneras': 'ternero',
    'vaca': 'vaca', 'vacas': 'vaca',
    'novillo': 'novillo', 'novillos': 'novillo',
    'vaquillona': 'vaquillona', 'vaquillonas': 'vaquillona',
    'toro': 'toro', 'toros': 'toro',
}

UNIDADES = 'litros?|lts?|l|kg|kilos?|tn|toneladas?|ton|bolsas?|cabezas?|unidades?|has?|hect[aá]reas?'

# Cuántos caracteres puede tener un importe escrito. El tope no es estético:
# con `[\d.,]+` seguido de "pesos", el motor prueba desde cada posición y
# consume todos los dígitos en cada intento — sobre un mensaje de 20.000
# dígitos eso son segundos, y crece al cuadrado. Un monto de 40 caracteres ya
# es absurdo, así que acotarlo lo vuelve lineal sin perder ningún caso real.
_LARGO_IMPORTE = 40

_RE_CATEGORIA = {p: re.compile(rf'\b{p}\b', _ASCII) for p in CATEGORIAS_ANIMAL}
_RE_MONTO_PESO = re.compile(r'\$' + _ESP + r'*([\d.,]+)', _ASCII)
_RE_MONTO_PALABRA = re.compile(r'([\d.,]{1,%d})' % _LARGO_IMPORTE + _ESP + r'*pesos', _ASCII)
_RE_QUITA_MONTO_PESO = re.compile(r'\$' + _ESP + r'*[\d.,]+', _ASCII)
_RE_QUITA_MONTO_PALABRA = re.compile(r'[\d.,]{1,%d}' % _LARGO_IMPORTE + _ESP + r'*pesos', _ASCII)
_RE_CANTIDAD = re.compile(rf'(\d+(?:[.,]\d+)?){_ESP}*({UNIDADES})?', _ASCII_I)
_RE_LOTE = re.compile(r'lote' + _ESP + r'*([a-zA-Z0-9]+)', _ASCII)
_RE_AYER = re.compile(r'\bayer\b', _ASCII)
_RE_ANTEAYER = re.compile(r'anteayer', _ASCII)
_RE_PRODUCTO_DE = re.compile(rf'de{_ESP}+([a-záéíóúñ]+(?:{_ESP}+[a-záéíóúñ]+)?)', _ASCII_I)
_RE_PUNTOS = re.compile(r'\.', _ASCII)
_RE_MILES_FINAL = re.compile(r'\.\d{3}$', _ASCII)
# El número con que empieza el texto: '12abc' da 12, y 'abc' da NaN.
_RE_PREFIJO_NUMERICO = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?', _ASCII)

# Sin \b: en modo ASCII una vocal acentuada no es parte de la palabra, así
# que después de "sí" no hay borde.
_RE_CONFIRMA = re.compile(
    r'^(s[ií]|sip|dale|ok(ey)?|oka|listo|correcto|confirmo|confirm[aá]|exacto|as[ií] es|de una|tal cual|s[ií] dale)(' + _ESP + r'|$|[,.!])',
    _ASCII)
_RE_PREGUNTA = re.compile(r'\?|cu[aá]nt|cu[aá]l|qu[eé]\b|tengo|hay\b|stock|mostr|dec[ií]me', _ASCII)
_RE_MARGEN = re.compile(r'margen', _ASCII)
_RE_STOCK = re.compile(r'stock|hacienda|animales|cabezas', _ASCII)
_RE_GAST = re.compile(r'gast', _ASCII)
_RE_VEND = re.compile(r'vend|venta', _ASCII)
_RE_NACI = re.compile(r'naci', _ASCII)
_RE_MUERTE = re.compile(r'muri|murieron|se murió|se murio|perd[ií]', _ASCII)
_RE_COMPR = re.compile(r'compr', _ASCII)
_RE_TRASLADO = re.compile(r'traslad|pas[eé]|mov[ií]', _ASCII)
_RE_SANIDAD = re.compile(r'vacun|desparasit|tratamiento|sanidad|dosis', _ASCII)
_RE_LABOR = re.compile(r'sembr|pulveric|fumig|cosech|apliqu|fertilic|ar[ée]\b|rastr|disc', _ASCII)
_RE_GASTO = re.compile(r'pagu[ée]|gast[ée]|abon[ée]|gasto', _ASCII)
_RE_COMPRA = re.compile(r'compr[ée]|compre|carg[ué]', _ASCII)


def _detectar_categoria_animal(t: str) -> str | None:
    for palabra, canon in CATEGORIAS_ANIMAL.items():
        if _RE_CATEGORIA[palabra].search(t):
            return canon
    return None


def _prefijo_numerico(s: str) -> float:
    m = _RE_PREFIJO_NUMERICO.match(s.lstrip())
    return float(m.group(0)) if m else NAN


def _finito(n: float) -> float | None:
    """El número si es finito; si no, None: se trata como si no se hubiera dicho.

    Un importe de 400 dígitos entra en un mensaje de WhatsApp y `float` lo
    convierte en infinito. Guardado, envenenaba la base: cada consulta que lo
    sumara ("¿cuánto gasté?") explotaba al formatearlo, para siempre.
    """
    return n if math.isfinite(n) else None


def _num(s: str) -> float:
    """Tolera "1.200.000" (miles con punto) y "2,5" (decimal con coma)."""
    x = s.strip()
    if ',' in x:
        x = _RE_PUNTOS.sub('', x).replace(',', '.', 1)   # sólo la primera coma: es el decimal
    elif len(_RE_PUNTOS.findall(x)) > 1:
        x = _RE_PUNTOS.sub('', x)
    elif _RE_MILES_FINAL.search(x):
        x = _RE_PUNTOS.sub('', x)
    return _prefijo_numerico(x)


def _extraer_monto(t: str) -> float | None:
    m = _RE_MONTO_PESO.search(t) or _RE_MONTO_PALABRA.search(t)
    return _finito(_num(m.group(1))) if m else None


def _extraer_cantidad_unidad(t: str) -> tuple[float | None, str | None]:
    sin_monto = _RE_QUITA_MONTO_PALABRA.sub(' ', _RE_QUITA_MONTO_PESO.sub(' ', t))
    m = _RE_CANTIDAD.search(sin_monto)
    if not m:
        return None, None
    return _finito(_num(m.group(1))), (m.group(2).lower() if m.group(2) else None)


def _extraer_lote_ref(t: str) -> str | None:
    m = _RE_LOTE.search(t)
    return m.group(1) if m else None


def _extraer_fecha(t: str) -> str:
    if _RE_AYER.search(t):
        return fecha_iso(-1)
    if _RE_ANTEAYER.search(t):
        return fecha_iso(-2)
    return fecha_iso(0)


CONOCIDOS = ['gasoil', 'gas oil', 'fertilizante', 'urea', 'semilla', 'agroquímico', 'agroquimico',
             'glifosato', 'herbicida', 'ración', 'racion', 'soja', 'maíz', 'maiz', 'trigo']


def _extraer_producto(t: str) -> str | None:
    for p in CONOCIDOS:
        if p in t:
            return p
    m = _RE_PRODUCTO_DE.search(t)
    return m.group(1).strip() if m else None


# --- Mock principal -------------------------------------------------------


def parse_mock(texto: str) -> ParsedIntent:
    t = recortar(texto.lower())
    base = ParsedIntent(intent='unknown', record_type=None, fields={}, query=None, confidence=0, raw_text=texto)

    # 1) Confirmación
    if _RE_CONFIRMA.search(t):
        return base.reemplazar(intent='confirm', confidence=0.99)

    categoria_animal = _detectar_categoria_animal(t)
    lote_ref = _extraer_lote_ref(t)
    cantidad, unidad = _extraer_cantidad_unidad(t)
    monto = _extraer_monto(t)
    fecha = _extraer_fecha(t)
    es_pregunta = bool(_RE_PREGUNTA.search(t))

    # 2) Consultas
    if es_pregunta:
        if _RE_MARGEN.search(t):
            return base.reemplazar(intent='query', confidence=0.92, query={'metric': 'margen', 'loteRef': lote_ref})
        if categoria_animal or _RE_STOCK.search(t):
            return base.reemplazar(intent='query', confidence=0.9,
                                   query={'metric': 'stock_animal', 'categoriaAnimal': categoria_animal})
        if _RE_GAST.search(t):
            return base.reemplazar(intent='query', confidence=0.88, query={'metric': 'gasto_total'})
        if _RE_VEND.search(t):
            return base.reemplazar(intent='query', confidence=0.88, query={'metric': 'venta_total'})

    # 3) Eventos de hacienda (hay categoría animal + verbo)
    if categoria_animal:
        tipo = None
        if _RE_NACI.search(t):
            tipo = 'nacimiento'
        elif _RE_MUERTE.search(t):
            tipo = 'muerte'
        elif _RE_COMPR.search(t):
            tipo = 'compra'
        elif _RE_VEND.search(t):
            tipo = 'venta'
        elif _RE_TRASLADO.search(t):
            tipo = 'traslado'
        if tipo:
            fields = {'categoriaAnimal': categoria_animal, 'eventoTipo': tipo,
                      'cantidad': cantidad, 'monto': monto, 'fecha': fecha}
            conf = 0.9 if cantidad is not None else 0.55
            return base.reemplazar(intent='create_record', record_type='evento_hacienda',
                                   fields=fields, confidence=conf)

    # 4) Sanidad
    if _RE_SANIDAD.search(t):
        producto = _extraer_producto(t)
        fields = {'producto': producto if producto is not None else 'sanidad',
                  'categoria': categoria_animal if categoria_animal is not None else 'todos',
                  'cantidad': cantidad, 'fecha': fecha}
        return base.reemplazar(intent='create_record', record_type='evento_sanitario',
                               fields=fields, confidence=0.82)

    # 5) Labores
    if _RE_LABOR.search(t):
        labor = _extraer_producto(t)
        fields = {'laborTipo': labor if labor is not None else 'labor', 'loteRef': lote_ref,
                  'monto': monto, 'fecha': fecha, 'descripcion': texto}
        return base.reemplazar(intent='create_record', record_type='labor',
                               fields=fields, confidence=0.85 if lote_ref else 0.7)

    # 6) Venta de grano / producto (sin categoría animal)
    if _RE_VEND.search(t):
        fields = {'producto': _extraer_producto(t), 'cantidad': cantidad, 'unidad': unidad,
                  'monto': monto, 'loteRef': lote_ref, 'fecha': fecha}
        conf = 0.88 if monto is not None else 0.6
        return base.reemplazar(intent='create_record', record_type='venta', fields=fields, confidence=conf)

    # 7) Gasto
    if _RE_GASTO.search(t):
        fields = {'producto': _extraer_producto(t), 'monto': monto, 'loteRef': lote_ref,
                  'fecha': fecha, 'descripcion': texto}
        conf = 0.85 if monto is not None else 0.5
        return base.reemplazar(intent='create_record', record_type='gasto', fields=fields, confidence=conf)

    # 8) Compra de insumo
    if _RE_COMPRA.search(t):
        fields = {'producto': _extraer_producto(t), 'cantidad': cantidad, 'unidad': unidad,
                  'monto': monto, 'loteRef': lote_ref, 'fecha': fecha}
        completo = fields['producto'] is not None and cantidad is not None
        return base.reemplazar(intent='create_record', record_type='insumo',
                               fields=fields, confidence=0.88 if completo else 0.62)

    return base


# --- Caminos con modelo (mismo shape de salida que el mock) --------------


def _instrucciones(hoy: str) -> str:
    """Prompt e instrucciones compartidos por Bedrock y el modelo local.

    La fecha de hoy va inyectada porque el modelo no tiene forma de saberla, y sin
    ella no puede resolver "ayer". El registro terminaba fechado hoy (normalize()
    completa la fecha faltante), que es peor que fallar: queda mal en la planilla
    y nadie se entera. El mock resuelve lo mismo en _extraer_fecha().
    """
    return (
        'Sos el parser de un ERP agropecuario argentino. Hoy es ' + hoy + '. Convertí el mensaje informal del productor en un JSON EXACTO:\n'
        '{\n'
        ' "intent": "create_record" | "query" | "confirm" | "unknown",\n'
        ' "recordType": "insumo"|"labor"|"gasto"|"venta"|"evento_hacienda"|"evento_sanitario"|null,\n'
        ' "fields": { "producto"?, "cantidad"?(number), "unidad"?, "monto"?(number), "loteRef"?, "categoriaAnimal"?("ternero"|"vaca"|"novillo"|"vaquillona"|"toro"), "eventoTipo"?("nacimiento"|"muerte"|"compra"|"venta"|"traslado"), "laborTipo"?, "fecha"?("YYYY-MM-DD"), "descripcion"? },\n'
        ' "query": { "metric": "stock_animal"|"margen"|"gasto_total"|"venta_total", "loteRef"?, "categoriaAnimal"? } | null,\n'
        ' "confidence": number 0..1\n'
        '}\n'
        'Reglas: montos como número sin separador de miles. "lote 4" => loteRef "4". Preguntas => intent "query".\n'
        'ANIMALES (terneros, vacas, novillos, vaquillonas, toros): recordType SIEMPRE "evento_hacienda" con su "eventoTipo", también cuando se compran o se venden. Nunca "venta" ni "gasto": si no es evento_hacienda, el stock no se descuenta.\n'
        'SANIDAD (vacunas, dosis, antiparasitarios como ivermectina, tratamientos) => "evento_sanitario", nunca "labor".\n'
        'FECHAS: devolvé siempre "fecha" en YYYY-MM-DD. "ayer" es el día anterior a ' + hoy + '; "anteayer", dos días antes. Si el mensaje no menciona ninguna fecha, usá ' + hoy + '.\n'
        'Respondé SOLO el JSON, sin texto extra.'
    )


def _ejemplos(hoy: str, ayer: str) -> list[tuple[str, dict[str, Any]]]:
    """Los ejemplos llevan fecha coherente con el "hoy" inyectado; si no, le estaría
    mostrando fechas que contradicen la regla que acaba de leer.
    """
    return [
        ('Compré 200 litros de gasoil para el lote 4',
         {'intent': 'create_record', 'recordType': 'insumo',
          'fields': {'producto': 'gasoil', 'cantidad': 200, 'unidad': 'L', 'loteRef': '4', 'fecha': hoy},
          'query': None, 'confidence': 0.95}),
        ('Nacieron 8 terneros',
         {'intent': 'create_record', 'recordType': 'evento_hacienda',
          'fields': {'categoriaAnimal': 'ternero', 'eventoTipo': 'nacimiento', 'cantidad': 8, 'fecha': hoy},
          'query': None, 'confidence': 0.95}),
        ('ayer vendí 30 novillos a 1.200.000 en total',
         {'intent': 'create_record', 'recordType': 'evento_hacienda',
          'fields': {'categoriaAnimal': 'novillo', 'eventoTipo': 'venta', 'cantidad': 30, 'monto': 1200000, 'fecha': ayer},
          'query': None, 'confidence': 0.95}),
        ('le di 3 dosis de ivermectina a las vacas del lote 2',
         {'intent': 'create_record', 'recordType': 'evento_sanitario',
          'fields': {'producto': 'ivermectina', 'categoriaAnimal': 'vaca', 'cantidad': 3, 'loteRef': '2', 'fecha': hoy},
          'query': None, 'confidence': 0.9}),
        ('¿Cuál es el margen del lote 1?',
         {'intent': 'query', 'recordType': None, 'fields': {},
          'query': {'metric': 'margen', 'loteRef': '1'}, 'confidence': 0.95}),
    ]


def _construir_mensajes(texto: str) -> list[dict[str, str]]:
    hoy = fecha_iso(0)
    msgs: list[dict[str, str]] = [{'role': 'system', 'content': _instrucciones(hoy)}]
    for u, a in _ejemplos(hoy, fecha_iso(-1)):
        msgs.append({'role': 'user', 'content': u})
        msgs.append({'role': 'assistant', 'content': a_json(a)})
    msgs.append({'role': 'user', 'content': texto})
    return msgs


#: Cómo se usa cada campo más adelante. Los numéricos entran en columnas REAL
#: o INTEGER; los de texto se concatenan en los mensajes y se bindean como TEXT.
_CAMPOS_NUMERICOS = ('cantidad', 'monto')
_CAMPOS_TEXTO = ('producto', 'unidad', 'moneda', 'loteRef', 'categoriaAnimal',
                 'eventoTipo', 'laborTipo', 'categoria', 'fecha', 'descripcion',
                 'metric')   # éste de la query, que se sanea igual que los campos


def _numero_valido(valor: Any) -> float | int | None:
    """Un número que se puede guardar y mostrar, o None.

    Descarta lo que no es número, el infinito y el NaN. Un entero gigante pasa a
    float: SQLite no acepta enteros de más de 64 bits y el INSERT tiraba
    OverflowError con el mensaje ya marcado como procesado.
    """
    if isinstance(valor, bool):
        return None
    n = valor if isinstance(valor, (int, float)) else numero(valor)
    if isinstance(n, int):
        if abs(n) <= 2 ** 53:
            return n
        try:
            n = float(n)
        except OverflowError:
            return None
    return n if math.isfinite(n) else None


def _campos_saneados(fields: Any) -> dict[str, Any]:
    """Deja los campos del modelo con los tipos que el pipeline espera.

    Un modelo devuelve el JSON que quiere, y de acá en adelante nadie vuelve a
    chequear tipos: `loteRef` se le pasa a `.strip()`, `unidad` se concatena a
    un string y `descripcion` se bindea a SQLite. Cada uno de esos revienta con
    un tipo distinto, y siempre tarde — con el movimiento ya guardado y el
    mensaje marcado como procesado, así que el productor no recibe nada y el
    reintento de Meta se descarta por duplicado.

    Los números que llegan como texto se convierten; los textos que llegan como
    número se pasan a texto (un `loteRef: 4` claramente quiso decir "lote 4").
    Lo que no es un valor simple —una lista, un objeto— se descarta: se trata
    como si el modelo no hubiera dicho nada de ese campo. Lo mismo un texto en
    blanco: un `loteRef` de puros espacios cargaba el movimiento al primer lote.
    """
    if not isinstance(fields, dict):
        return {}
    salida: dict[str, Any] = {}
    for clave, valor in fields.items():
        if valor is None:
            continue
        if clave in _CAMPOS_NUMERICOS:
            n = _numero_valido(valor)
            if n is not None:
                salida[clave] = n
            continue
        if clave in _CAMPOS_TEXTO:
            if isinstance(valor, (int, float)) and not isinstance(valor, bool):
                valor = texto_numero(valor)
            if isinstance(valor, str) and recortar(valor):
                salida[clave] = recortar(valor)
            # Cualquier otra cosa —o un texto en blanco— se descarta.
            continue
        salida[clave] = valor
    return salida


def _normalizar_salida(p: Any, texto: str) -> ParsedIntent:
    if not isinstance(p, dict):
        p = {}
    # Una confianza que no es un número finito vale como ausente. Un NaN, por
    # ejemplo, hacía que `confianza < umbral` diera siempre False: se guardaba
    # sin pedir confirmación.
    confidence = _numero_valido(p.get('confidence'))
    query = p.get('query')
    return ParsedIntent(
        intent=p.get('intent') if p.get('intent') is not None else 'unknown',
        record_type=p.get('recordType'),
        fields=_campos_saneados(p.get('fields')),
        # Una `query` que no es un objeto se trata como si no viniera: más
        # adelante se le pide `.get('metric')` sin preguntar. Si lo es, se sanea
        # igual que los campos: un `loteRef` numérico ahí adentro también
        # terminaba en un `.strip()`.
        query=_campos_saneados(query) if isinstance(query, dict) else None,
        confidence=confidence if confidence is not None else 0.8,
        raw_text=texto,
    )


def _post(url: str, headers: dict[str, str], body: str, timeout: float) -> tuple[int, str]:
    """POST con plazo total. Devuelve (status, texto); un 4xx/5xx no tira, se reporta."""
    r = pedir('POST', url, plazo=timeout, headers=headers, cuerpo=body)
    return r.status, r.texto


def _parse_local(texto: str) -> ParsedIntent:
    """Camino local: modelo chico vía Ollama (ej. qwen2.5:3b, llama3.2:3b).

    Corre en tu propia máquina; no requiere key ni costo por token.
    """
    cuerpo = a_json({'model': config.local_model, 'stream': False, 'format': 'json',
                     'options': {'temperature': 0}, 'messages': _construir_mensajes(texto)})
    status, texto_res = _post(f'{config.ollama_url}/api/chat',
                              {'Content-Type': 'application/json'}, cuerpo, TIMEOUT_MODELO_S)
    if not 200 <= status < 300:
        raise RuntimeError(f'ollama {status}')
    data = json.loads(texto_res)
    return _normalizar_salida(json.loads(data['message']['content']), texto)


def _parse_bedrock(texto: str) -> ParsedIntent:
    """Camino C: Amazon Nova Lite sobre Bedrock, vía la API Converse. La firma va a
    mano (service/sigv4.py) para no traer el SDK de AWS.

    Converse no tiene un equivalente a `response_format`: el JSON se pide por
    prompt y recortarlo es responsabilidad nuestra.
    """
    msgs = _construir_mensajes(texto)
    cuerpo = a_json({
        'system': [{'text': m['content']} for m in msgs if m['role'] == 'system'],
        'messages': [{'role': m['role'], 'content': [{'text': m['content']}]}
                     for m in msgs if m['role'] != 'system'],
        'inferenceConfig': {'temperature': 0, 'maxTokens': 512},
    })

    req = firmar_aws(
        method='POST',
        host=f'bedrock-runtime.{config.aws_region}.amazonaws.com',
        path=f'/model/{percent_encode(config.bedrock_model_id)}/converse',
        region=config.aws_region,
        service='bedrock',
        body=cuerpo,
        creds=AwsCreds(
            access_key_id=config.aws_access_key_id,
            secret_access_key=config.aws_secret_access_key,
            session_token=config.aws_session_token or None,
        ),
    )

    status, texto_res = _post(req.url, req.headers, req.body, TIMEOUT_MODELO_S)
    if not 200 <= status < 300:
        raise RuntimeError(f'bedrock {status}: {texto_res[:200]}')
    data = json.loads(texto_res)
    contenido = (((data or {}).get('output') or {}).get('message') or {}).get('content') or []
    crudo = contenido[0].get('text', '') if contenido else ''
    return _normalizar_salida(json.loads(_recortar_json(crudo)), texto)


_RE_CERCA_INICIO = re.compile(rf'^{_ESP}*```(?:json)?{_ESP}*', _ASCII_I)
_RE_CERCA_FIN = re.compile(rf'{_ESP}*```{_ESP}*$', _ASCII)


def _recortar_json(s: str) -> str:
    """Sin JSON mode, el modelo a veces envuelve la respuesta en ```json … ``` o le
    cuelga una frase. Nos quedamos con el objeto más externo.
    """
    limpio = _RE_CERCA_FIN.sub('', _RE_CERCA_INICIO.sub('', s, count=1), count=1)
    i = limpio.find('{')
    j = limpio.rfind('}')
    return limpio[i:j + 1] if i >= 0 and j > i else limpio


# Probe único y cacheado: ¿está Ollama disponible? (no chequea en cada mensaje)
_ollama_probe: bool | None = None
_probe_lock = threading.Lock()


def ollama_disponible() -> bool:
    global _ollama_probe
    # El lock evita que dos mensajes simultáneos disparen dos probes: el
    # primero lo corre y el segundo espera ese resultado.
    with _probe_lock:
        if _ollama_probe is None:
            try:
                _ollama_probe = pedir('GET', f'{config.ollama_url}/api/tags', plazo=0.8).ok
            except Exception:
                _ollama_probe = False
        return _ollama_probe


def _motor_auto() -> str | None:
    """Motor elegido en modo 'auto': Bedrock primero, porque es el camino financiado
    por la universidad. El probe de Ollama solo corre si no hay credenciales de
    AWS, para no sumarle 800 ms al primer mensaje.
    """
    if use_bedrock():
        return 'bedrock'
    if ollama_disponible():
        return 'local'
    return None


def parse(texto: str) -> ParsedIntent:
    """Selector de modo. Un modo explícito prueba solo su motor; 'auto' elige por
    credencial disponible. Cualquier falla del modelo cae al mock.
    """
    mode = config.parser_mode
    motor = _motor_auto() if mode == 'auto' else (None if mode == 'mock' else mode)
    try:
        if motor == 'bedrock':
            return _parse_bedrock(texto).reemplazar(motor='bedrock')
        if motor == 'local':
            return _parse_local(texto).reemplazar(motor='local')
    except Exception as e:
        # Caer al mock sin decir nada deja al bot parseando con reglas y a nadie
        # enterado: la calidad baja y el log se ve igual que siempre.
        print(f'⚠️  parser: {motor} falló ({e}) — cae al mock', file=sys.stderr)
    return parse_mock(texto).reemplazar(motor='mock')


def parser_activo() -> str:
    """Describe qué motor quedará activo (para el log de arranque)."""
    mode = config.parser_mode
    if mode == 'mock':
        return 'mock (reglas determinísticas)'
    if mode == 'bedrock':
        return (f'Bedrock · {config.bedrock_model_id} ({config.aws_region})'
                if use_bedrock() else 'mock (faltan credenciales AWS)')
    if mode == 'local':
        return (f'local · {config.local_model} (Ollama)'
                if ollama_disponible() else 'mock (Ollama no responde)')
    if use_bedrock():
        return f'Bedrock · {config.bedrock_model_id} ({config.aws_region}, auto)'
    if ollama_disponible():
        return f'local · {config.local_model} (Ollama, auto)'
    return 'mock (auto: sin credenciales ni Ollama)'
