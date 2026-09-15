# Parva — MVP

ERP agropecuario simplificado que se carga y consulta por **WhatsApp** (texto o voz)
y se ve en una **web app**. Este repo es el MVP del **sistema de datos + backend
del bot**, con WhatsApp e IA **mockeados** para poder correrlo sin
ninguna cuenta externa.

> Diseño completo en [`docs/superpowers/specs/2026-06-22-parva-mvp-design.md`](docs/superpowers/specs/2026-06-22-parva-mvp-design.md).

## Requisitos

- **Node.js ≥ 24** (usa `node:sqlite` y ejecución nativa de TypeScript).
- **Nada más.** Cero dependencias: no hay `npm install`, ni Docker, ni base externa.

## Cómo correr

```bash
node src/server.ts
# luego abrir http://localhost:3000
```

La base SQLite (`data/parva.db`) y los datos de ejemplo se crean solos en el primer
arranque. Scripts equivalentes:

```bash
npm start        # node src/server.ts
npm run dev      # con --watch (recarga al editar)
npm run seed     # recarga datos de ejemplo
npm run reset    # borra y recarga la base
```

## Qué tiene el MVP

- **Web app** (`/web`): dashboard con la "planilla" (movimientos), lotes y márgenes,
  hacienda y sanidad — y un **simulador de WhatsApp** embebido para chatear con el bot.
- **Backend del bot** (`/src`): el flujo completo
  `mensaje → transcribe → parse → normalize → validate → persist`, con permisos por
  rol, guardrails y aislamiento por tenant.
- **2 productores de ejemplo** que muestran las dos variantes:
  - *Estancia La Esperanza* — **agrícola** (lotes, movimientos, márgenes).
  - *Don Pedro e Hijos* — **ganadero** (hacienda, eventos, sanidad).

## Qué está mockeado (y cómo se haría real)

| Pieza | Default (`sim`) | Real (cambio por env) |
|---|---|---|
| WhatsApp | Simulador en la web + `POST /webhook/whatsapp` con la forma real de Meta | **Meta Cloud API** con `WHATSAPP_MODE=meta` (ver abajo) |
| Parser IA | Mock determinístico en español (reglas) | **Nova Lite sobre Bedrock** con credenciales AWS (ver abajo), GPT-4o mini con `OPENAI_API_KEY`, o modelo local vía Ollama |
| Transcripción | Devuelve el texto (no hay audio) | Whisper / gpt-4o-mini-transcribe (aún no cableado) |
| Base de datos | SQLite (`node:sqlite`) | Postgres / Supabase |

Variables en `.env.example`. Los scripts de npm cargan `.env` automáticamente
(`--env-file-if-exists`); si corrés `node src/server.ts` a mano, no se carga.

## Parser real (Amazon Nova Lite sobre Bedrock)

Es el camino por default cuando hay credenciales AWS en el entorno, por delante
de OpenAI: el crédito de Bedrock está cubierto por la universidad mientras el
proyecto sea con fines educativos, y OpenAI saldría del bolsillo.

Bedrock no acepta una API key en un header como OpenAI: cada request va firmado
con SigV4. La firma está implementada en `src/services/sigv4.ts` con `node:crypto`
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
eval "$(aws configure export-credentials --profile TU_PERFIL --format env)" && npm start
```

En el arranque el log dice qué motor quedó activo:

```
Parser activo: Bedrock · amazon.nova-lite-v1:0 (us-east-2, auto)
```

Si dice `mock`, faltan las credenciales o venció la sesión.

### Por qué Nova Lite y no Micro

Para extraer slots a JSON en una sola pasada, Nova Micro sería mejor candidato:
text-only, más barato y con menos latencia, que en un bot donde el productor
espera la respuesta importa. Queda descartado por permisos, no por criterio:
Micro y Pro exigen perfil de inferencia entre regiones, esos perfiles rutean a
`us-west-2`, y el rol de SSO del curso tiene un deny explícito fuera de
`us-east-2`. Con una cuenta propia habría que volver a medirlo.

## Eval del parser

`eval/casos.json` tiene 40 mensajes anotados con lo que el parser debería sacar
de cada uno. Ninguno replica los ejemplos few-shot del prompt: todos miden
generalización, no memoria.

```bash
npm run eval -- --motor=mock      # baseline, sin credenciales ni costo
npm run eval -- --motor=bedrock   # Nova Lite
npm run eval -- --caso=hacienda-singular
npm run eval -- --strict          # exit 1 si algo falla
```

No es un test unitario y no corre solo: los motores con modelo cuestan plata y
no son determinísticos. Se corre a mano al tocar el prompt o cambiar de modelo.

Medición al 2026-09-15, sobre los 43 casos:

| Motor | Correctos |
| :---- | ----: |
| mock (reglas) | 19/43 (44 %) |
| Nova Lite | 40/43 y 42/43 en dos corridas |

**Una sola corrida no alcanza para concluir nada.** Dos corridas idénticas de
Nova Lite pueden dar el mismo total fallando casos distintos, y acá el rango
entre corridas fue de dos casos. Antes de atribuirle una diferencia a un cambio
de prompt, correlo al menos dos veces.

Falla estable conocida: `labor-rastra` ("rastreé el lote 3"). En español general
"rastrear" es seguir un rastro, y el sentido agronómico (pasar la rastra) no le
sale. Se arregla listándole los verbos de labor en el prompt, pero eso sería
parchear el único caso que el eval todavía marca: queda como señal a propósito.

## WhatsApp real (Meta Cloud API)

Con `WHATSAPP_MODE=meta` el bot responde por WhatsApp de verdad: verifica la
firma `X-Hub-Signature-256`, deduplica los reintentos de Meta por `msg.id`,
contesta el webhook al instante y recién después procesa (en cola por remitente,
para no romper el flujo de confirmación).

> ⚠️ El modo `sim` deja el webhook **sin autenticar**. No lo expongas a internet.

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
cp .env.example .env       # y completar las META_* + WHATSAPP_MODE=meta
npm run link-phone -- --list
npm run link-phone -- 1 +54911XXXXXXXX     # tu celular → Juan Pérez (owner)
npm start                                   # verificá que diga "modo meta" sin faltantes

npm run tunnel                              # cloudflared, sin cuenta
# o: ngrok http 3000   (requiere cuenta, pero su inspector en :4040 muestra
#                       los bytes crudos y la firma que mandó Meta)
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
npm run check-meta                 # deduce la WABA del token (necesita META_APP_ID)
npm run check-meta -- <WABA_ID>    # o pasásela a mano
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

`npm run check-meta` verifica los cuatro pasos de una.

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

**Permisos:** en el simulador, elegí el usuario *gestor_campo* y pedí `¿cuál es el
margen?` → el bot lo **deniega** (el gestor no ve info económica).

**Confirmación:** mandá algo ambiguo como `compré gasoil` (sin cantidad) → el bot
**pide confirmar**; respondé `sí` y lo registra.

El webhook real de Meta se puede ejercitar con:

```bash
curl -X POST http://localhost:3000/webhook/whatsapp -H 'Content-Type: application/json' \
  -d '{"entry":[{"changes":[{"value":{"messages":[{"from":"5491100000003","type":"text","text":{"body":"Nacieron 5 terneros"}}]}}]}]}'
```

## Arquitectura (resumen)

```
web/                 Front vanilla (dashboard + simulador WhatsApp)
src/
  server.ts          HTTP (node:http): web app, API, webhook Meta
  db.ts / repo.ts    SQLite + repositorio (toda query filtra por tenant)
  seed.ts            Datos de ejemplo (agrícola + ganadero)
  permissions.ts     Matriz de permisos por rol (guardrail)
  pipeline/          transcriber → parser → normalizer → validator → process
  services/          margin (márgenes por lote) · query (lecturas del bot) · export (CSV)
```

**Fuente de verdad:** la base estructurada. La "planilla" es una vista + export CSV
(resuelve el riesgo de la planilla editable libre). Un **margen** solo se muestra si
es confiable; si faltan ventas o costos, se indica en vez de mostrar un número falso.
