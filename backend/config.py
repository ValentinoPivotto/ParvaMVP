"""Configuración por entorno. El parser corre sin secrets; WhatsApp exige las META_*."""
import math
import os
import sys
from pathlib import Path

from .formato import numero

# Raíz del proyecto: este archivo vive en backend/, así que un nivel arriba.
RAIZ = Path(__file__).resolve().parent.parent


def _cargar_env(ruta: Path) -> None:
    """Carga el .env al entorno del proceso.

    **Lo que ya está en el entorno gana sobre el archivo.** De eso depende el
    flujo de Bedrock del README: `eval "$(aws configure export-credentials …)"`
    deja las credenciales en el shell y el .env las tiene vacías. Con la
    precedencia al revés, el archivo las pisaría con '' y el parser caería al
    mock sin explicación.

    Si el archivo no existe, no pasa nada: el .env es opcional.
    """
    try:
        texto = ruta.read_text(encoding='utf-8')
    except OSError:
        return

    for clave, valor in _parsear_env(texto).items():
        os.environ.setdefault(clave, valor)


def _parsear_env(texto: str) -> dict[str, str]:
    """Interpreta el contenido de un .env.

    Tres reglas que no son obvias y que un .env cualquiera ya usa:

    - **Comentario al final de la línea.** Todo lo que sigue a un `#` fuera de
      comillas se descarta. `PORT=3000 # local` vale 3000; sin esto el valor
      quedaba en "3000 # local" y el server ni arrancaba.
    - **Un `#` entre comillas es parte del valor**, que es lo que salva a un
      token o un secreto que lo contenga.
    - **Un valor entre comillas puede ocupar varias líneas**, para una clave
      privada pegada tal cual.

    Una clave repetida: gana la última.
    """
    valores: dict[str, str] = {}
    lineas = texto.splitlines()
    i = 0
    while i < len(lineas):
        s = lineas[i].strip()
        i += 1
        if not s or s.startswith('#'):
            continue
        if s.startswith('export '):
            s = s[len('export '):].lstrip()
        if '=' not in s:
            continue
        clave, _, valor = s.partition('=')
        valor = valor.lstrip()

        if valor[:1] in ('"', "'", '`'):
            comilla = valor[0]
            resto = valor[1:]
            cierre = resto.find(comilla)
            while cierre < 0 and i < len(lineas):
                resto += '\n' + lineas[i]
                i += 1
                cierre = resto.find(comilla)
            # Lo que venga después de la comilla de cierre es comentario.
            valor = resto[:cierre] if cierre >= 0 else resto
        else:
            corte = valor.find('#')
            if corte >= 0:
                valor = valor[:corte]
            valor = valor.strip()

        valores[clave.strip()] = valor
    return valores


_cargar_env(RAIZ / '.env')


def _numero_o_default(clave: str, por_defecto: float) -> float:
    """Lee una variable numérica; si no se entiende, avisa y usa el default.

    Quedarse con el valor roto es peor que ignorarlo: un PORT ilegible corta el
    arranque con un error que no dice cuál era la variable, y un umbral de
    confianza ilegible deja NaN, que desactiva el pedido de confirmación sin
    que nada lo anuncie.
    """
    crudo = os.environ.get(clave)
    if crudo is None or not crudo.strip():
        # Una variable vacía se lee como ausente, no como cero. `PORT=` daba un
        # puerto al azar y `CONFIDENCE_THRESHOLD=` dejaba el umbral en 0, que
        # es lo mismo que no pedir confirmación nunca.
        return por_defecto
    n = numero(crudo, por_defecto)
    if isinstance(n, float) and math.isnan(n):
        print(f'⚠️  {clave}={crudo!r} no es un número; se usa {por_defecto}', file=sys.stderr)
        return por_defecto
    return n


def _env(clave: str, por_defecto: str = '') -> str:
    """`process.env.X ?? por_defecto`: una variable vacía es un valor, no una ausencia."""
    valor = os.environ.get(clave)
    return por_defecto if valor is None else valor


class _Config:
    # La base por defecto sigue en la raíz del proyecto, independiente del cwd.
    # Un DB_PATH relativo explícito conserva su resolución desde el cwd.
    port = int(_numero_o_default('PORT', 3000))
    db_path = _env('DB_PATH', str(RAIZ / 'data' / 'parva.db'))

    # Modo del parser: auto | mock | local | bedrock
    #  - auto (default): bedrock si hay credenciales AWS → modelo local si Ollama
    #    responde → mock.
    #  - local: usa un modelo chico vía Ollama (server local en :11434).
    parser_mode = _env('PARSER_MODE', 'auto')
    local_model = _env('LOCAL_MODEL', 'qwen2.5:3b')
    ollama_url = _env('OLLAMA_URL', 'http://localhost:11434')

    # Bedrock (Nova Lite). Las credenciales del SSO son temporales y vencen; se
    # exportan al entorno con `aws configure export-credentials` (ver README).
    # Región us-east-2 por default: el rol del curso tiene deny explícito fuera
    # de ahí, y los modelos que exigen perfil de inferencia entre regiones rutean
    # a us-west-2 y fallan con AccessDenied.
    aws_region = _env('AWS_REGION', 'us-east-2')
    aws_access_key_id = _env('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = _env('AWS_SECRET_ACCESS_KEY')
    aws_session_token = _env('AWS_SESSION_TOKEN')
    bedrock_model_id = _env('BEDROCK_MODEL_ID', 'amazon.nova-lite-v1:0')

    # Umbral de confianza del parser para pedir confirmación antes de persistir.
    # Si el valor no se entiende se usa el default en vez de quedarse con NaN:
    # `confianza < NaN` es siempre False, así que un typo apagaba el pedido de
    # confirmación por completo, sin un solo error a la vista.
    confidence_threshold = _numero_o_default('CONFIDENCE_THRESHOLD', 0.7)

    # WhatsApp: Meta Cloud API, único camino. El webhook exige firma válida y las
    # respuestas salen por la Graph API; sin las META_* el bot no contesta.
    meta_verify_token = _env('META_VERIFY_TOKEN', 'parva-dev')
    meta_app_id = _env('META_APP_ID')
    meta_app_secret = _env('META_APP_SECRET')
    meta_access_token = _env('META_ACCESS_TOKEN')
    meta_phone_number_id = _env('META_PHONE_NUMBER_ID')
    meta_graph_version = _env('META_GRAPH_VERSION', 'v25.0')

    # Responder a números no registrados. Default off: cuesta plata por
    # conversación y degrada el quality rating del número.
    meta_reply_to_unknown = _env('META_REPLY_TO_UNKNOWN') == '1'


config = _Config()


def use_bedrock() -> bool:
    """¿Hay credenciales de AWS en el entorno para firmarle a Bedrock?"""
    return len(config.aws_access_key_id.strip()) > 0 and len(config.aws_secret_access_key.strip()) > 0


def falta_config_meta() -> list[str]:
    """Variables que faltan para hablar con Meta ([] = listo para arrancar)."""
    faltan: list[str] = []
    if not config.meta_access_token.strip():
        faltan.append('META_ACCESS_TOKEN')
    if not config.meta_phone_number_id.strip():
        faltan.append('META_PHONE_NUMBER_ID')
    if not config.meta_app_secret.strip():
        faltan.append('META_APP_SECRET')
    return faltan
