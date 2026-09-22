"""Configuración por entorno. El parser corre sin secrets; WhatsApp exige las META_*."""
import os
from pathlib import Path

from .jscompat import numero

# Raíz del proyecto: este archivo vive en backend/, así que un nivel arriba.
RAIZ = Path(__file__).resolve().parent.parent


def _cargar_env(ruta: Path) -> None:
    """Carga el .env al entorno del proceso.

    Antes lo hacían los scripts de npm con `node --env-file-if-exists=.env`; sin
    npm hay que hacerlo desde el código. Se respeta la misma precedencia que
    usaba Node: **lo que ya está en el entorno gana sobre el archivo**. De eso
    depende el flujo de Bedrock del README — `eval "$(aws configure
    export-credentials …)"` deja las credenciales en el shell y el .env las
    tiene vacías; al revés, el archivo las pisaría con '' y el parser caería al
    mock sin explicación.

    Si el archivo no existe no pasa nada, igual que con `--env-file-if-exists`.
    """
    try:
        texto = ruta.read_text(encoding='utf-8')
    except OSError:
        return

    valores: dict[str, str] = {}
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith('#'):
            continue
        if s.startswith('export '):
            s = s[len('export '):].lstrip()
        if '=' not in s:
            continue
        clave, _, valor = s.partition('=')
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in '\'"`':
            valor = valor[1:-1]
        valores[clave.strip()] = valor   # una clave repetida: gana la última

    for clave, valor in valores.items():
        os.environ.setdefault(clave, valor)


_cargar_env(RAIZ / '.env')


def _env(clave: str, por_defecto: str = '') -> str:
    """`process.env.X ?? por_defecto`: una variable vacía es un valor, no una ausencia."""
    valor = os.environ.get(clave)
    return por_defecto if valor is None else valor


class _Config:
    # La base por defecto sigue en la raíz del proyecto, independiente del cwd.
    # Un DB_PATH relativo explícito conserva su resolución desde el cwd.
    port = int(numero(os.environ.get('PORT'), 3000))
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
    confidence_threshold = numero(os.environ.get('CONFIDENCE_THRESHOLD'), 0.7)

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
