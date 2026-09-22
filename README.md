# Parva — MVP

ERP agropecuario simplificado que se carga y consulta por **WhatsApp** y se ve en
una **web app**. El productor le escribe al bot desde su celular, por WhatsApp de
verdad, y el dato aparece en el dashboard.

> Diseño completo en [`docs/superpowers/specs/2026-06-22-parva-mvp-design.md`](docs/superpowers/specs/2026-06-22-parva-mvp-design.md).

## Requisitos

- **Python ≥ 3.11** (usa `sqlite3` y `http.server` de la biblioteca estándar).
- **Nada más para ejecutar.** Cero dependencias de ejecución, sin `pip install`,
  sin entorno virtual, sin Docker ni base externa.

El código lleva anotaciones de tipo en todo el backend. No hace falta ningún
chequeador para ejecutarlo; si querés validarlas en el editor, cualquier
`mypy`/`pyright` instalado aparte las lee sin configuración extra.

Los tests del backend corren con `unittest` de la stdlib. Para los del frontend
hace falta **Node.js** (el frontend es JavaScript de navegador y sus tests corren
con el runner de Node); no se necesita para usar la aplicación.

## Cómo correr

```bash
python3 -m backend.handler.server
# luego abrir http://localhost:3000
```

Eso levanta la web app con los datos de ejemplo. Para que el **bot** conteste
hacen falta las credenciales de Meta y un túnel: ver
[WhatsApp real](#whatsapp-real-meta-cloud-api).

La base SQLite (`data/parva.db`, en la raíz del proyecto) y los datos de ejemplo se
crean solos en el primer arranque. La ruta por defecto es independiente del
directorio desde el que se ejecute Python; un `DB_PATH` relativo se resuelve desde
ese directorio de ejecución. El `Makefile` tiene los atajos:

```bash
make start       # python3 -m backend.handler.server
make dev         # recarga al guardar un .py
make seed        # recarga datos de ejemplo
make reset       # borra y recarga la base
make test        # tests: backend (Python) + frontend (necesita Node)
```

## Qué tiene el MVP

- **Web app** (`frontend/`): dashboard con la "planilla" (movimientos), lotes y
  márgenes, hacienda y sanidad. Se refresca solo cada 5 s mientras la pestaña
  está visible, así lo que se manda por WhatsApp aparece sin recargar.
- **Backend del bot** (`backend/`): el flujo completo
  `mensaje → transcribe → parse → normalize → validate → persist`, con permisos por
  rol, guardrails y aislamiento por tenant.
- **2 productores de ejemplo** que muestran las dos variantes:
  - *Estancia La Esperanza* — **agrícola** (lotes, movimientos, márgenes).
  - *Don Pedro e Hijos* — **ganadero** (hacienda, eventos, sanidad).

## Qué es real y qué falta

| Pieza | Hoy | Pendiente |
|---|---|---|
| WhatsApp | **Meta Cloud API**: webhook firmado + envío por Graph API | Número propio verificado en vez del de test |
| Parser IA | **Nova Lite sobre Bedrock** con credenciales AWS, o modelo local vía Ollama; sin ninguno cae a reglas determinísticas en español | Set de mensajes anotados para comparar los tres |
| Transcripción | Sólo texto: un audio devuelve un placeholder | Amazon Transcribe |
| Base de datos | SQLite (`sqlite3` de la stdlib) | Postgres / Supabase |

Variables en `.env.example`. El `.env` se carga solo al arrancar, corras como
corras. **El entorno tiene prioridad sobre el archivo**: una variable ya exportada
en la shell gana, que es lo que hace funcionar el flujo de credenciales de AWS de
más abajo.

## Parser real (Amazon Nova Lite sobre Bedrock)

Es el camino por default cuando hay credenciales AWS en el entorno. Parva corre
con modelos de Amazon o con un modelo local, y con nada más: el crédito de
Bedrock está cubierto por la universidad mientras el proyecto sea con fines
educativos, y un proveedor facturado aparte saldría del bolsillo.

Bedrock no acepta una API key en un header: cada request va firmado con SigV4. La firma está implementada en `backend/service/sigv4.py` con `hmac`/`hashlib`
para no traer el SDK de AWS y mantener la promesa de cero dependencias. Está
verificada contra el canonical request que imprime el propio AWS CLI.

### Correrlo

Las credenciales del SSO son temporales y vencen; cuando el parser vuelva a caer
al mock sin explicación, es esto. Renovarlas:

```bash
aws sso login --profile TU_PERFIL
```

Exportarlas al entorno y levantar el server:

```bash
eval "$(aws configure export-credentials --profile TU_PERFIL --format env)" && make start
```

En el arranque el log dice qué motor quedó activo:

```
Parser activo: Bedrock · amazon.nova-lite-v1:0 (us-east-2, auto)
```

Ese log dice qué motor **se eligió**, no que funcione: solo comprueba que las
variables de AWS no estén vacías. Con la sesión vencida las variables siguen
cargadas, así que va a decir `Bedrock` igual y recién va a fallar al primer
mensaje. La señal real es el warning por mensaje:

```
⚠️  parser: bedrock falló (bedrock 403: ...) — cae al mock
```

Si aparece eso, el bot sigue contestando con el mock (peor calidad, sin errores
visibles para el productor) y hay que renovar la sesión. Si el log de arranque
dice `mock`, directamente no había credenciales.

### Por qué Nova Lite y no Micro

Para extraer slots a JSON en una sola pasada, Nova Micro sería mejor candidato:
text-only, más barato y con menos latencia, que en un bot donde el productor
espera la respuesta importa. Queda descartado por permisos, no por criterio:
Micro y Pro exigen perfil de inferencia entre regiones, esos perfiles rutean a
`us-west-2`, y el rol de SSO del curso tiene un deny explícito fuera de
`us-east-2`. Con una cuenta propia habría que volver a medirlo.

### Lo que falta

No hay todavía un set de mensajes anotados para comparar mock, Nova Lite y el
modelo local sobre la misma entrada. Sin eso, la elección de motor es una
intuición y no una medición.

## WhatsApp real (Meta Cloud API)

Es el único camino de entrada: el bot verifica la firma `X-Hub-Signature-256`
(sin `META_APP_SECRET` **rechaza todo**), deduplica los reintentos de Meta por
`msg.id`, contesta el webhook al instante y recién después procesa (en cola por
remitente, para no romper el flujo de confirmación).

### 1. Configurar la app en Meta

1. **developers.facebook.com** → crear cuenta de developer.
2. **Create App** → caso de uso **Business** → nombre (ej. `parva-dev`).
3. **Add product → WhatsApp → Set up.** Meta provisiona una WABA de test y un
   número de test gratis.
4. En **API Setup**, copiar a tu `.env`:
   - **Phone number ID** → `META_PHONE_NUMBER_ID` (ID numérico largo, **no** el
     `+1 555…` de al lado — confundirlos da error 100).
   - **Temporary access token** → `META_ACCESS_TOKEN` (dura **24 h**).
   - La versión del curl de ejemplo (ej. `v23.0`) → `META_GRAPH_VERSION`.
5. **Registrar tu celular**: mismo panel, dropdown "To" → Manage phone number
   list → tu número → te llega un código → ingresarlo. **Máximo 5 destinatarios**;
   saltear esto da error **131030**.
6. **Settings → Basic → App Secret** → `META_APP_SECRET`. Hacelo **antes** del
   paso 8: el server falla cerrado y sin el secret todo webhook devuelve 401.

### 2. Levantar el server y el túnel

```bash
cp .env.example .env       # y completar las META_*
make link-phone ARGS="--list"
make link-phone ARGS="1 +54911XXXXXXXX"    # tu celular → Juan Pérez (owner)
make start                                  # verificá que no liste faltantes

make tunnel                                 # ngrok hacia el puerto del .env
# su inspector en :4040 muestra los bytes crudos y la firma que mandó Meta
```

Antes de pegar la URL en Meta, comprobá que el handshake ya funciona — si esto no
devuelve `ok`, el "Verify and save" del panel va a fallar:

```bash
curl -sS "https://<túnel>/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=$META_VERIFY_TOKEN&hub.challenge=ok"
```

En ambos túneles gratis la URL cambia en cada restart y hay que re-pegarla en Meta.

### 3. Conectar el webhook

En **WhatsApp → Configuration → Webhook → Edit**:

- Callback URL: `https://<túnel>/webhook/whatsapp`
- Verify token: exactamente tu `META_VERIFY_TOKEN` → **Verify and save**
- **Después, aparte: "Manage" → suscribirse al campo `messages`.** Es un paso
  distinto de verificar la URL, y saltearlo es *la* falla más común: el GET
  verifica bien y no llega un solo mensaje.

Ahora escribile al número de test desde tu celular. Si no llega nada, mirá la
terminal: `[wa] número no registrado: <from>` te muestra el formato exacto que
mandó Meta y el comando para vincularlo.

**Nota sobre la ventana de 24 h:** como Parva solo responde a mensajes
entrantes, todo envío cae dentro de la ventana → texto libre permitido, sin
necesidad de aprobar templates. Si el token vence (error 190), regeneralo en el
panel o creá uno permanente en Business Settings → System Users.

### 4. Chequear el setup antes de la demo

```bash
make check-meta                          # deduce la WABA del token (necesita META_APP_ID)
make check-meta ARGS="<WABA_ID>"         # o pasásela a mano
```

El argumento es el **WABA ID**, no el `phone_number_id`: son dos IDs numéricos
largos indistinguibles a ojo. Si cargás `META_APP_ID` en el `.env`, el script se
la pregunta a Meta y no hace falta pasarla.

Valida las cuatro cosas que hacen que un número "Conectado" no conteste: que el
token llegue al `META_PHONE_NUMBER_ID`, que la callback URL guardada en Meta siga
viva (los túneles gratis cambian de URL en cada restart), en qué WABA está el
número, y si la app está suscrita **a esa** WABA. Traduce los códigos de Meta
(100, 190, 200, 133010) al arreglo que corresponde.

Si algo no se puede chequear lo dice como **chequeo incompleto** y sale con
código 1: un check salteado nunca se reporta como OK.

### 5. Pasar del número de test al número propio

El `+1 555…` que da Meta es un número de test: sólo habla con 5 destinatarios
allow-listeados y no se puede renombrar. Para que el bot aparezca como **Parva**:

1. **WhatsApp Manager → Números de teléfono → Agregar número.** Cargá tu número,
   elegí el nombre a mostrar y verificá con el código que llega por SMS o llamada.
   El número queda en estado **Conectado**.
2. **Copiá el Phone number ID nuevo** (el de tu número, no el del de test) a
   `META_PHONE_NUMBER_ID`.
3. **Suscribí la app a la WABA de ese número.** Si el número quedó en una WABA
   distinta de la de test, la suscripción del webhook **no se hereda** y no llega
   ni un mensaje. Se chequea con:

   ```bash
   curl -s "https://graph.facebook.com/$META_GRAPH_VERSION/<WABA_ID>/subscribed_apps" \
     -H "Authorization: Bearer $META_ACCESS_TOKEN"
   # vacío => suscribir:
   curl -s -X POST "https://graph.facebook.com/$META_GRAPH_VERSION/<WABA_ID>/subscribed_apps" \
     -H "Authorization: Bearer $META_ACCESS_TOKEN"
   ```

4. **Usá un token que alcance esa WABA.** El token temporal del panel API Setup
   está scopeado a la WABA de test: contra la propia da error 190 o 200. Creá uno
   permanente en *Business Settings → System Users* con la WABA asignada.

`make check-meta` verifica los cuatro pasos de una.

> El bot responde **desde el número que recibió el mensaje**, no desde
> `META_PHONE_NUMBER_ID`. Con el número de test y el propio en la misma WABA, los
> dos entran por el mismo webhook y cada uno contesta en su propio chat. El log
> `[wa] ← <remitente> → nuestro número <id>` dice cuál recibió qué.

**No hace falta verificar el negocio para esto.** La verificación sirve para subir
los límites (>250 contactos únicos/24 h) y sumar números; el nombre que se muestra
es el *display name* del número, que es un trámite aparte.

## Probalo (mensajes de ejemplo)

**Agrícola** (productor *Estancia La Esperanza*):
- `Compré 200 litros de gasoil para el lote 4`
- `Pagué $600.000 de fumigación en el lote 4`
- `Vendí 240 tn de soja por $9.600.000`
- `¿Cuál es el margen del lote 1?`

**Ganadero** (productor *Don Pedro e Hijos*):
- `Nacieron 8 terneros`
- `Se murieron 2 vacas`
- `¿Cuántos terneros tengo?`
- `Vacuné 120 vacas contra la aftosa`

**Permisos:** escribí desde el celular de un usuario con rol *gestor_campo*
(`make link-phone ARGS="--list"`) y pedí `¿cuál es el margen?` → el bot lo
**deniega** (el gestor no ve info económica).

**Confirmación:** mandá algo ambiguo como `compré gasoil` (sin cantidad) → el bot
**pide confirmar**; respondé `sí` y lo registra. El recibo dice qué quedó sin
cargar (`✅ Registré gasoil. No me dijiste el monto.`): un registro incompleto
entra si lo pedís, pero el mensaje no afirma lo que no guardó.

**Dato imposible:** `nacieron terneros` (sin cantidad) no ofrece registrarlo,
porque sin la cantidad el evento no entra en la base. Pide el dato y da un
ejemplo de la forma que sí entiende.

### Sin celular a mano

Se le puede pegar al webhook directamente, pero hay que firmar el body igual que
Meta (`printf` sin `\n`: un byte de más cambia el HMAC):

```bash
set -a && . ./.env && set +a
BODY='{"entry":[{"changes":[{"value":{"messages":[{"id":"wamid.prueba1","from":"5491100000003","type":"text","text":{"body":"Nacieron 5 terneros"}}]}}]}]}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$META_APP_SECRET" | awk '{print $NF}')"
curl -X POST http://localhost:3000/webhook/whatsapp \
  -H 'Content-Type: application/json' -H "X-Hub-Signature-256: $SIG" \
  --data-binary "$BODY"
```

La respuesta del curl es sólo el ACK: el bot contesta **por WhatsApp**, así que
va a intentar mandarle un mensaje real a ese número por la Cloud API. Cambiá el
`id` en cada prueba o el dedup se lo come, y mirá el log y el dashboard, no el
body de la respuesta.

## Arquitectura y recorrido de un mensaje

El backend se organiza en las tres capas **handler → service → repository**.
`handler` cumple el papel de un controller: recibe HTTP y devuelve la respuesta.

| Capa | Responsabilidad | Archivos principales |
|---|---|---|
| `backend/handler/` | Rutas HTTP, estáticos y transporte de Meta | `server.py`, `whatsapp.py` |
| `backend/service/` | Orquestación de mensajes, modelos, reglas y resultados del negocio | `process.py`, `parser.py`, `normalizer.py`, `validator.py`, `permissions.py`, `query.py`, `margin.py` |
| `backend/repository/` | Lecturas, escrituras, agregaciones SQL, auditoría y esquema SQLite | `repo.py`, `db.py`, `seed.py` |

```text
backend/
  handler/
    server.py        Entrada HTTP y arranque del servidor
    whatsapp.py      Firma del webhook, lectura del envelope y envío a Meta
  service/
    process.py       Orquesta el recorrido y las confirmaciones
    transcriber.py   Devuelve el texto; audio todavía sin conectar
    parser.py        Reglas determinísticas, prompts y adaptadores Bedrock/Ollama
    normalizer.py    Resuelve lotes y unidades
    validator.py     Decide aceptar, pedir confirmación o denegar
    permissions.py   Permisos por rol
    query.py         Respuestas a consultas del bot
    margin.py        Cálculo y confiabilidad del margen
    export.py        Generación de CSV
    dashboard.py     Arma el estado de la web
    sigv4.py         Firma de las llamadas del parser a Bedrock
  repository/
    repo.py          Acceso a los datos y auditoría
    db.py            Conexión, esquema y migraciones
    seed.py          Datos de ejemplo; también ejecutable con make seed/reset
  scripts/           link_phone.py, check_meta.py, tunnel.py, dev.py
  config.py          Configuración por entorno, .env y ruta de la base
  types.py           Tipos compartidos del dominio y mensajes
  phone.py           Normalización de teléfonos compartida
  formato.py         Números, fechas y JSON en los formatos que usa la app
frontend/            index.html, app.js, styles.css y brandbook.html
data/                SQLite local (ignorado por Git)
```

El mismo proceso Python sirve `frontend/` y la API. El `Makefile` y el `.env`
siguen en la raíz; no hay un build ni un despliegue separado para el frontend.
`brandbook.html` sigue siendo una referencia local que se abre directamente.

Para seguir **«Compré 200 litros de gasoil para el lote 4»** en el código:

1. [`handler/server.py`](backend/handler/server.py) recibe el mensaje en el
   webhook (`POST /webhook/whatsapp`), verifica la firma y contesta el ACK.
   Busca el remitente por teléfono en el repositorio para obtener usuario, rol y
   productor, y llama a `process_message(sender, texto)`.
2. [`service/process.py`](backend/service/process.py) llama a `transcribe`
   (hoy devuelve el texto), guarda el mensaje original en `raw_message` y llama
   a `parse`. También coordina las consultas y las confirmaciones de pendientes.
3. [`service/parser.py`](backend/service/parser.py) interpreta la intención y
   extrae los campos: un insumo, gasoil, cantidad 200, unidad litros y referencia
   al lote 4. Aquí están las reglas de respaldo y los prompts/adaptadores de los
   modelos.
4. [`service/normalizer.py`](backend/service/normalizer.py) busca ese lote dentro
   del productor mediante el repositorio y convierte la unidad a `L`.
5. [`service/validator.py`](backend/service/validator.py) aplica
   [`permissions.py`](backend/service/permissions.py), los campos requeridos, la
   resolución del lote y el umbral de confianza. `process_message` usa esa decisión
   para continuar, dejar un pendiente de confirmación o denegar el registro.
6. Si se acepta, `process_message` llama a `insert_movimiento` en
   [`repository/repo.py`](backend/repository/repo.py), que guarda el movimiento y
   su auditoría usando [`db.py`](backend/repository/db.py). La respuesta vuelve al
   handler, que la manda por la Cloud API; el dashboard la levanta en el próximo
   refresco.

Las consultas del bot pasan por `service/query.py`; las reglas del margen están
en `service/margin.py` y las sumas SQL en el repositorio. Las lecturas simples de
identidad y del listado de productores se hacen desde el handler al repositorio.

### Tests

```bash
make test            # backend (Python, unittest) + frontend (Node)
make test-golden     # regenera test/golden/ — mirá el git diff antes de commitear
```

La mayoría son **tests de caracterización**: corren una pieza y comparan la
salida contra un archivo en `test/golden/`. Esos archivos son el contrato — el
texto exacto que el bot contesta, los bytes exactos del CSV que se baja el
productor, las respuestas HTTP con sus headers. Son cosas que no se pueden
cambiar sin que alguien lo note, así que tampoco se cambian sin querer.

| Archivo | Qué congela |
|---|---|
| `test_parser.py` | las reglas determinísticas sobre 115 mensajes reales |
| `test_pipeline.py` | 230 interacciones punta a punta: respuesta del bot + base resultante |
| `test_http.py` | las 24 respuestas HTTP con status, headers y cuerpo |
| `test_seed.py` | los datos de ejemplo y el esquema |
| `test_export.py` | los CSV de las tres planillas |
| `test_prompt.py` | el prompt que se le manda al modelo |
| `test_sigv4.py` | la firma de Bedrock, con vectores fijos |
| `test_phone.py`, `test_formato.py` | teléfonos, montos, fechas y JSON |
| `test_concurrencia.py` | el stock bajo escrituras en paralelo (ver abajo) |

Los tests no tocan `data/parva.db` ni leen tu `.env`: `test/__init__.py` fija un
entorno propio con una base temporal antes de importar el backend.

Un golden que cambia **no es** un permiso para regenerarlo. Primero hay que
mirar el diff y decidir si el cambio era la intención.

### Concurrencia

El servidor atiende cada request en su propio hilo y el pipeline del bot corre
en una cola por remitente, de modo que dos usuarios del **mismo** productor (el
dueño y el gestor de campo) pueden estar escribiendo a la vez.

La conexión a SQLite está protegida por un lock, pero eso sólo serializa cada
sentencia. Una secuencia como «leé el stock, sumale 8, guardalo» son tres, y
entre la primera y la última se puede colar otro hilo: los dos leen el mismo
stock y el último `UPDATE` se come el delta del otro.

Para eso está `db.transaccion()`, que sostiene el lock durante toda la secuencia
y la envuelve en una transacción de SQLite. La regla: **si una operación son
varias sentencias y tiene que valer como una sola, va adentro de un
`transaccion()`**. Hoy son las escrituras compuestas (`insert_movimiento`,
`adjust_hacienda`, `insert_evento_hacienda`, `insert_evento_sanitario`) y las
lecturas que arman una respuesta a partir de varias consultas (`build_state`,
`answer_query`, `margen_por_lote`, `totales_por_lote`).

`test/test_concurrencia.py` lo cubre: sin la transacción, ocho hilos cargando
nacimientos a la vez dejan los eventos registrados y el stock corto.

**Fuente de verdad:** la base estructurada. La "planilla" es una vista + export CSV
(resuelve el riesgo de la planilla editable libre). Un **margen** solo se muestra si
es confiable; si faltan ventas o costos, se indica en vez de mostrar un número falso.
