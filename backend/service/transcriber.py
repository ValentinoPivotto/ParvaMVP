"""Transcripción de notas de voz.

Un mensaje de texto pasa de largo. La Cloud API entrega el AUDIO (no el texto),
así que Parva tendría que transcribir del lado del back: el camino real todavía
NO está cableado, iría por Amazon Transcribe para no depender de un proveedor
que no sea AWS. Mientras tanto, un audio devuelve un placeholder.
"""


def transcribe(texto: str | None = None, audio_url: str | None = None) -> str:
    """`texto`: mensaje de texto, ya viene listo.
    `audio_url`: nota de voz, URL del media descargado de la Cloud API.
    """
    if texto is not None:
        return texto

    # Sin transcripción cableada, un audio no se puede leer todavía.
    return '[audio sin transcribir]'
