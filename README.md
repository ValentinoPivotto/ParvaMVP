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

| Pieza | En el MVP | En producción (cambio por env) |
|---|---|---|
| WhatsApp | Simulador en la web + endpoint `POST /webhook/whatsapp` con la **forma real de Meta** | Meta Cloud API (tokens + verify webhook) |
| Parser IA | Mock determinístico en español (reglas) | GPT-4o mini con `OPENAI_API_KEY` |
| Transcripción | Devuelve el texto (no hay audio) | Whisper / gpt-4o-mini-transcribe |
| Base de datos | SQLite (`node:sqlite`) | Postgres / Supabase |

Variables en `.env.example` (`PORT`, `DB_PATH`, `OPENAI_API_KEY`, `CONFIDENCE_THRESHOLD`).

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
