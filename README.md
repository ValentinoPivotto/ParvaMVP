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
| Parser IA | Mock determinístico en español (reglas) | GPT-4o mini con `OPENAI_API_KEY`, o modelo local vía Ollama |
| Transcripción | Devuelve el texto (no hay audio) | Whisper / gpt-4o-mini-transcribe (aún no cableado) |
| Base de datos | SQLite (`node:sqlite`) | Postgres / Supabase |

Variables en `.env.example`. Los scripts de npm cargan `.env` automáticamente
(`--env-file-if-exists`); si corrés `node src/server.ts` a mano, no se carga.

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

cloudflared tunnel --url http://localhost:3000    # sin cuenta
# o: ngrok http 3000   (requiere cuenta, pero su inspector en :4040 muestra
#                       los bytes crudos y la firma que mandó Meta)
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

---

ℹ️ **Este proyecto NO usa git** (decisión del equipo). No correr `git init` ni commitear.
