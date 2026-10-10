"""Normalización de teléfonos (pura, sin I/O).

El problema real: el `wa_id` que manda Meta para números argentinos suele
OMITIR el 9 de móvil (un +54 9 11 1234-5678 llega como "541112345678"), y la
dirección del quirk no está documentada de forma confiable. Por eso no se
adivina cuál forma llega: se buscan TODAS las variantes en la DB y se responde
siempre al `from` exacto que mandó Meta (ver handler/whatsapp.py).
"""
import re

AR = '54'

_NO_DIGITO = re.compile(r'[^0-9]')


def solo_digitos(raw: str) -> str:
    """Solo dígitos. Tolera '+', espacios, guiones, paréntesis y prefijo '00'."""
    d = _NO_DIGITO.sub('', raw or '')
    return d[2:] if d.startswith('00') else d


def _es_ar_sin_9(d: str) -> bool:
    """¿Es un móvil AR al que le falta el 9? (54 + 10 dígitos, sin 9 adelante)"""
    return d.startswith(AR) and d[2:3] != '9' and len(d) == 12


def normalize_telefono(raw: str) -> str:
    """Forma canónica E.164 con '+'. Para móviles AR agrega el 9 si falta:

    '541112345678'   -> '+5491112345678'
    '5491112345678'  -> '+5491112345678'
    '+5491100000001' -> '+5491100000001'   (los sembrados ya son canónicos)
    """
    d = solo_digitos(raw)
    if not d:
        return ''
    return '+' + (AR + '9' + d[2:] if _es_ar_sin_9(d) else d)


def phone_variants(raw: str) -> list[str]:
    """Formas candidatas para buscar en `usuario.telefono`, en orden de prioridad y
    sin duplicados: tal cual → con '+' → canónica AR-con-9 → AR-sin-9.

    El orden importa: un teléfono ya canónico (como los sembrados) matchea en la
    primera consulta, sin pasar por las variantes.
    """
    d = solo_digitos(raw)
    if not d:
        return []

    out: list[str] = []

    def push(v: str) -> None:
        if v and v not in out:
            out.append(v)

    push(raw)                       # tal cual vino (cubre los sembrados con '+')
    push('+' + d)                   # sin separadores
    push(normalize_telefono(d))

    # Variante sin el 9: solo para AR móvil ya canónico (54 9 + 10 dígitos).
    if d.startswith(AR + '9') and len(d) == 13:
        push('+' + AR + d[3:])

    return out


def to_wa_id(raw: str) -> str:
    """Forma del `wa_id` para ENVIAR por la Cloud API.

    Meta entrega el `from` de los móviles argentinos CON el 9, pero tanto la lista
    de autorizados del número de prueba como el wa_id canónico de AR lo quieren
    SIN el 9. Responder al `from` tal cual da error 131030; sacando el 9, entra.
      '5491178310248' -> '541178310248'
    Solo toca AR móvil (549 + 10 dígitos); cualquier otro número queda igual.
    """
    d = solo_digitos(raw)
    if d.startswith(AR + '9') and len(d) == 13:
        return AR + d[3:]
    return d
