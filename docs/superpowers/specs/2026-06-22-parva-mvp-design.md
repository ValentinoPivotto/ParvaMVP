# Parva — Diseño del MVP

- **Fecha:** 2026-06-22
- **Fuente:** `Parva_PRD_v1.md.pdf`, §12 (Decisiones pendientes y próximos pasos)
- **Estado:** aprobado para implementación — modo de corrida **local con mocks**

---

## 1. Contexto y objetivo

Parva es un ERP simplificado para productores agropecuarios de 100 a 1.000 ha en Argentina y
Uruguay. La carga y consulta se hacen por WhatsApp (texto o voz); el análisis se ve en una
plataforma web. La hipótesis central: el productor querría interactuar con su Excel (su fuente de
datos) por WhatsApp, porque ya usa los dos y no le exige cambiar de herramienta.

Según §12 del PRD, **este carril** es responsable de tres ítems. Este documento los diseña como un
**MVP ejecutable** (decisión del usuario: enfoque "C"):

1. **Sistema de datos** — el spreadsheet definitivo y sus variantes: hojas, campos, fórmulas,
   cómo interactúan.
2. **Backend** — la lógica del back: cómo se parsea, normaliza, valida y guarda el dato.
3. **Pricing y economía unitaria** — costo por usuario activo (WhatsApp + IA), precio, margen, TAM.

## 2. Alcance y límites de rol

| Construye este proyecto | NO construye (otros carriles) |
| --- | --- |
| Modelo de datos + generador del spreadsheet | UI de onboarding y branding |
| Backend Fastify (webhook, parser, normalizer, validator, persistencia) | Web app / flow de frontend |
| Permisos por rol + guardrails + aislamiento por tenant | Entrevistas y experimento de validación |
| Data API (contrato que consume el onboarding) + export/import CSV/XLSX | — |
| Modelo de pricing/economía unitaria | — |

Para el frontend se entrega **el contrato de API** y un **dev-harness** (CLI/HTTP) que demuestra el
loop completo. El dev-harness es herramienta de desarrollo, **no** la UI del producto.

## 3. Decisión arquitectónica central: fuente de verdad

El riesgo #1 del PRD es "planilla editable libre vs estructura normalizada": si el usuario edita
cualquier celda libremente, rompe la estructura de la que dependen el bot y los cálculos. Se
adopta el **Plan B del PRD como Plan A**:

> **Postgres (vía Prisma) es la única fuente de verdad. El "spreadsheet" es una vista editable con
> rieles (campos tipados, no celdas libres) más export/import `.csv`/`.xlsx`.**

Consecuencias:

- El bot escribe en la DB; la vista/planilla se regenera → la promesa "el spreadsheet se llena
  solo" queda intacta.
- La edición vuelve por celdas tipadas validadas contra el esquema, no texto libre → no rompe
  normalización, cálculos ni el bot.
- Habilita explícitamente lo que construye este proyecto: **aislamiento estricto por tenant, permisos por rol,
  guardrails**.
- Los **márgenes** son vistas computadas en el backend; las fórmulas viven en código, no en celdas
  frágiles. El `.xlsx` exportado incluye fórmulas legibles para que el productor las vea, pero la
  verdad es el cálculo del back.

**Alternativa descartada:** Google Sheets como store vivo. Se siente más "planilla real" pero hace
los guardrails, el aislamiento y la normalización mucho más frágiles — precisamente el riesgo #1.

## 4. Entregable 1 — Sistema de datos

### 4.1 Multi-tenancy y aislamiento

`tenantId` (= Productor/owner) presente en **toda** entidad de negocio. Todo método de repositorio
exige `tenantId`; no existe lectura cross-tenant. Es la primera línea de los guardrails.

### 4.2 Entidades (modelo Prisma)

**Identidad / acceso**
- `Productor` — tenant root. Datos del owner, país (AR/UY), `tipoCampo` (agrícola/ganadero/mixto).
- `Usuario` — pertenece a un `Productor`. Campos: nombre, teléfono (E.164, clave de match del
  webhook), `rol`.
- `Rol` (enum app-level por portabilidad SQLite/PG): `owner`, `gestor_campo`. El `contador` es
  externo (solo recibe CSV), no es usuario del sistema.

**Estructura productiva**
- `Campo` — establecimiento. Pertenece al tenant. Nombre, ubicación, ha totales.
- `Lote` — unidad de manejo dentro de un Campo. Nombre/número, ha, uso actual.
- `Campaña` — período productivo (ej. "Trigo 2025/26"). Fechas inicio/fin, cultivo o actividad.
- `LoteCampaña` — relación N:M Lote×Campaña con el cultivo/actividad asignado y ha sembradas.

**Gestión diaria (operación)**
- `Labor` — tarea a campo (siembra, pulverización, cosecha, etc.). Lote, campaña, fecha, tipo,
  responsable, costo asociado opcional.
- `Insumo` — compra/uso de insumo (semilla, fertilizante, agroquímico, gasoil…). Producto, cantidad,
  unidad canónica, precio unitario, lote/campaña, fecha.
- `Gasto` — egreso no-insumo (servicios, fletes, arrendamiento, mano de obra). Categoría, monto,
  moneda, lote/campaña opcional, fecha.
- `Ingreso` — venta (grano, hacienda, etc.). Producto, cantidad, precio, monto, lote/campaña, fecha.

**Ganadero**
- `Hacienda` — stock por categoría (vacas, vaquillonas, terneros, novillos, toros…). Cantidad actual
  por categoría y campo.
- `EventoHacienda` — nacimiento, muerte, compra, venta, traslado, cambio de categoría. Mutación que
  ajusta el stock. Categoría, cantidad, fecha, lote/campo, motivo.
- `EventoSanitario` — vacunación / tratamiento. Producto, categoría afectada, cantidad, fecha.

**Contable / financiero / planificación**
- `MovimientoContable` — asiento simple (ingreso/egreso) con categoría contable, para la página
  contable/financiera. Deriva de Ingreso/Gasto + cargas manuales.
- `Factura` — comprobante. Tipo, número, proveedor/cliente, monto, fecha, link al gasto/ingreso.
- `PlanItem` — ítem de estrategia/planificación (objetivo, presupuesto, fecha objetivo).

**Soporte / trazabilidad**
- `Margen` — **computado** (no se persiste como verdad; se calcula on-demand y se cachea opcional).
  Por `Lote` y por `Campaña`: ingresos − (insumos + labores + gastos imputables). Incluye flag
  `confiable` (ver §4.4).
- `RawMessage` — mensaje entrante crudo (texto/transcripción), parseo, `confidence`, estado
  (`pending` / `confirmed` / `discarded`). Trazabilidad + guardrail de confirmación.
- `AuditLog` — quién editó qué y cuándo (versionado; riesgo del PRD). Entidad, campo, valor previo,
  valor nuevo, usuario, timestamp, origen (bot/web/import).

### 4.3 Variantes agrícola / ganadero / mixto

La variante (`Productor.tipoCampo`) define qué hojas/campos están activos:

- **Agrícola:** Lote/Campaña con cultivos, Labores agrícolas, Insumos, Ingresos por grano. Márgenes
  por cultivo/lote/campaña. Oculta Hacienda/EventoHacienda.
- **Ganadero:** Hacienda, EventoHacienda, EventoSanitario, Ingresos por hacienda. Márgenes por
  actividad ganadera. Lote/Campaña opcional (potreros).
- **Mixto:** ambas activas.

### 4.4 Principio de confiabilidad del margen

Un margen se muestra **solo si la info que lo sustenta es confiable**. Si faltan datos para
calcularlo (ej. no hay ingresos cargados, o falta costo de un insumo clave), Parva **lo indica** en
vez de mostrar un número que aparenta certeza. `Margen.confiable=false` + razones faltantes.

### 4.5 Generador de spreadsheet

Package `data` expone un generador que exporta el dato normalizado del tenant a un **workbook
`.xlsx` multi-hoja**, una hoja por página del MVP:

`Gestión diaria` · `Insumos` · `Gastos` · `Ventas/Ingresos` · `Contable-Financiero` · `Facturas` ·
`Sanidad` (ganadero) · `Hacienda` (ganadero) · `Planificación` · `Márgenes`.

- La hoja `Márgenes` incluye **fórmulas** (`=SUMIF(...)`) legibles, además del valor computado.
- Las hojas activas dependen de la variante del tenant.
- Import inverso: parsea Excel/CSV del usuario (paso final opcional del onboarding) con límites
  (máximo de columnas, tipos permitidos) y reporta errores en vez de romper.

## 5. Entregable 2 — Backend (Fastify, ejecutable)

### 5.1 Flujo de escritura (carga por bot)

```
Webhook (forma Meta Cloud API) → guarda RawMessage (pending)
  → (si audio) Transcripción (Whisper | mock) → texto
  → Parser (GPT-4o mini structured outputs | mock) → ParsedIntent
  → Normalizer (resuelve lote/campaña/categoría, unidades canónicas, fechas relativas)
  → Validator (zod + reglas de negocio + permiso por rol + ownership + umbral de confianza)
       ├─ confianza < umbral OR ambigüedad → responde pidiendo confirmación (queda pending)
       └─ ok → continúa
  → Persistencia (Prisma, tenant-scoped) + AuditLog
  → Responde confirmando ("✅ Registré 200 L de gasoil en el lote 4")
```

### 5.2 ParsedIntent (esquema de structured output)

```jsonc
{
  "intent": "create_record" | "query" | "edit_record" | "unknown",
  "recordType": "labor" | "insumo" | "gasto" | "venta" | "evento_hacienda" | "evento_sanitario" | null,
  "fields": {
    "producto": "string?", "cantidad": "number?", "unidad": "string?",
    "monto": "number?", "moneda": "string?", "loteRef": "string?",
    "categoriaAnimal": "string?", "fecha": "ISO date?", "descripcion": "string?"
  },
  "query": { "metric": "stock_animal" | "margen" | "gasto_total" | "...", "filters": {} } | null,
  "confidence": 0.0,
  "rawText": "string"
}
```

El **mock** del parser emula esto con reglas/regex (detecta "compré", "litros/L", "lote N",
"se murió", "cuántos", "margen", montos y fechas relativas) — suficiente para correr el loop sin
OpenAI.

### 5.3 Flujo de lectura (consulta por bot)

`intent=query` → módulo de consultas → agregado **tenant-scoped** → respuesta en lenguaje natural.
Ejemplos del PRD: "¿cuántos terneros tengo?" (suma `Hacienda` categoría=ternero), "¿cuál es el
margen?" (lee `Margen` del lote/campaña; si `confiable=false`, lo aclara).

### 5.4 Matriz de permisos por rol

| Acción | owner | gestor_campo | contador |
| --- | --- | --- | --- |
| Crear Labor / Insumo / Gasto | ✅ | ✅ | ✗ |
| Crear EventoHacienda / EventoSanitario | ✅ | ✅ | ✗ |
| Crear Venta/Ingreso | ✅ | ✗ | ✗ |
| Leer estructura (lotes/campañas) | ✅ | ✅ (referencia) | ✗ |
| Leer Márgenes / Contable / Ventas | ✅ | ✗ | ✗ |
| Export CSV | ✅ | ✗ | recibe el CSV |
| Gestionar usuarios | ✅ | ✗ | ✗ |

### 5.5 Guardrails

- **Aislamiento por tenant** forzado en la capa de datos (todo repo exige `tenantId`).
- **Chequeo de rol** en validator/servicio antes de toda escritura/lectura sensible.
- **Confirmación** ante baja confianza/ambigüedad: el registro queda `pending` hasta confirmar.
- **Límites de entrada:** largo máximo de mensaje, máximo de columnas en import, tipos permitidos.
- El bot **nunca** ejecuta links externos ni fórmulas; las fórmulas se computan server-side.
- **AuditLog** en toda escritura/edición.

### 5.6 Superficie HTTP (apps/api)

- `POST /webhook/whatsapp` — ingress forma Meta Cloud API (+ `GET` verify para el handshake real).
- `GET /api/onboarding/schema` y `POST /api/onboarding/...` — contrato para la web app.
- `GET /api/export/:tenantId.csv` · `.xlsx` — export.
- `POST /api/import` — import Excel/CSV con validación.
- `GET /healthz`.

## 6. Entregable 3 — Pricing y economía unitaria

Modelo `.xlsx` generado por script, con `assumptions.md` que documenta cada supuesto y lo marca como
**a validar**. Estructura:

**Inputs (supuestos, a validar contra pricing público):**
- WhatsApp (Meta Cloud API): costo por conversación/mensaje para AR/UY. Nota: Meta migró a pricing
  por mensaje (utility/marketing/auth) con mensajes de servicio gratis dentro de la ventana de 24 h;
  un bot mayormente reactivo cae bastante en "servicio". Se modela un costo mensual mezclado.
- GPT-4o mini: precio por 1M tokens in/out. Parse promedio ≈ 500 in + 150 out.
- Whisper: precio por minuto. Audio promedio ≈ 0,5 min.
- Infra: Railway + Vercel + Supabase, fijo mensual amortizado entre N usuarios.

**Escenarios de uso:** liviano / promedio / pesado (mensajes/mes, % audio vs texto, tokens/msg).

**Outputs:**
- Costo por usuario activo/mes (desglosado: WhatsApp + parse + transcripción + infra).
- Margen bruto a USD 15/mes; sensibilidad del margen vs uso y vs % audio.
- **Break-even** de uso (a qué nivel de mensajes el usuario deja de ser rentable).
- TAM: refinamiento desde 64.206 explotaciones (PRD) × precio; SAM/SOM si el xlsx de Módulo 2 aporta.

Opcional: validar precios de Meta/OpenAI con web search (el PRD lista "analizar pricing de Meta
Cloud API" como pendiente).

## 7. Stack, repo y cómo corre

Monorepo **pnpm** (activado vía corepack; fallback npm workspaces si corepack falla):

```
parva/
  packages/
    data/        Prisma schema, client, seed, generador xlsx/csv, import
    core/        parser, normalizer, validator, permissions, guardrails (lógica pura + tests)
    config/      carga y validación de env
  apps/
    api/         Fastify: webhook, bot read/write, data API, export/import
    dev-harness/ CLI para simular mensajes WhatsApp end-to-end (solo dev)
  pricing/       modelo .xlsx + script generador + assumptions.md
  docs/          este spec + README
```

### 7.1 Modos de corrida (intercambiables por env)

| Capa | Default (este MVP) | Producción |
| --- | --- | --- |
| DB | SQLite (cero infra) | Postgres / Supabase |
| IA parse | Mock determinístico | GPT-4o mini (`OPENAI_API_KEY`) |
| Transcripción | Mock | OpenAI Whisper |
| WhatsApp | Payloads simulados + CLI | Meta Cloud API (tokens + webhook) |

Variables: `DATABASE_URL`, `DB_PROVIDER` (`sqlite`|`postgresql`), `OPENAI_API_KEY` (vacío → mock),
`WHATSAPP_MODE` (`sim`|`meta`), `META_*` (tokens, phone number id, verify token).

**Portabilidad SQLite/Postgres:** los enums se modelan como `String` con validación en código (los
enums Prisma no corren en SQLite), para mantener un único schema. El salto a Postgres es cambiar
`DB_PROVIDER` + `DATABASE_URL` y migrar.

### 7.2 Resultado esperable hoy (local, sin secrets)

`pnpm dev` levanta la API; el dev-harness manda un "mensaje" (ej. *"compré 200 litros de gasoil para
el lote 4"*) → parser mock → normalizer → validator → persiste en SQLite → `pnpm gen:sheet`
regenera el `.xlsx` → consulta del margen por el harness. Loop completo verificable sin OpenAI,
Docker ni Meta.

## 8. Estrategia de testing

- `packages/core`: tests unitarios de parser(mock)/normalizer/validator/permissions (lógica pura).
- `packages/data`: tests de repos (aislamiento por tenant: un tenant nunca lee datos de otro) y del
  cálculo de margen (incluido el caso `confiable=false`).
- `apps/api`: test de integración del flujo webhook→persistencia con mocks.
- TDD donde aplique (la lógica de core y los guardrails son los candidatos naturales).

## 9. Supuestos y preguntas abiertas

- Precios de Meta/OpenAI: supuestos a validar (web search opcional).
- Categorías de hacienda y unidades canónicas: se arranca con un set base (extensible).
- Umbral de confianza para confirmación: parámetro configurable (arranca en 0,7).
- El nombre del subdirectorio de código es `parva/` dentro de la carpeta del TFG.

## 10. Fuera de alcance (MVP)

Consultas avanzadas a IA ("¿me conviene vender?"), clima/drones/maquinaria, app móvil nativa,
integraciones con otros sistemas, agente en grupos de WhatsApp, plan Pro, multi-campo avanzado,
pagos vía MercadoPago (queda como gancho de §7 del PRD pero no entra en este build ahora).
La UI de onboarding/branding y el experimento de validación tampoco.
