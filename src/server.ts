// Servidor HTTP con node:http (sin dependencias). Sirve la web app, la API del
// dashboard, el endpoint del simulador de WhatsApp y el webhook con la forma
// real de Meta Cloud API (mockeado: no requiere cuenta de Meta).
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { config, modoMeta, faltaConfigMeta } from './config.ts';
import { initSchema } from './db.ts';
import { seedIfEmpty } from './seed.ts';
import * as repo from './repo.ts';
import { processMessage } from './pipeline/process.ts';
import { parserActivo } from './pipeline/parser.ts';
import { margenPorLote } from './services/margin.ts';
import { exportCsv } from './services/export.ts';
import { verificarFirma, extraerEntrantes, enviarTexto, type MensajeEntrante } from './whatsapp.ts';

const WEB_DIR = fileURLToPath(new URL('../web/', import.meta.url));
const MAX_BODY = 1024 * 1024; // 1 MB: los webhooks de Meta son chicos

function sendJson(res: ServerResponse, code: number, data: unknown): void {
  const body = JSON.stringify(data);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(body);
}

/**
 * Lee el body como Buffer.
 *
 * Acumular chunks en un string (`data += c`) stringifica cada chunk por separado
 * y parte los caracteres multi-byte que caen en el borde: "Compré" se convierte
 * en "Compr��". Además de corromper el texto, cambia el HMAC — con acentos en
 * casi todos los mensajes, la firma fallaría de forma intermitente según cómo
 * TCP parta los paquetes.
 */
function readBodyBuffer(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on('data', (c: Buffer) => {
      size += c.length;
      if (size > MAX_BODY) { reject(new Error('body demasiado grande')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

const readBody = (req: IncomingMessage): Promise<string> =>
  readBodyBuffer(req).then((b) => b.toString('utf8'));

const TIPOS: Record<string, string> = { html: 'text/html', css: 'text/css', js: 'text/javascript', json: 'application/json', svg: 'image/svg+xml' };

async function serveStatic(res: ServerResponse, file: string): Promise<void> {
  try {
    const buf = await readFile(WEB_DIR + file);
    const ext = file.split('.').pop() ?? 'html';
    res.writeHead(200, { 'Content-Type': `${TIPOS[ext] ?? 'text/plain'}; charset=utf-8` });
    res.end(buf);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('No encontrado');
  }
}

// Estado completo del dashboard para un tenant.
function buildState(productorId: number) {
  const productor = repo.getProductor(productorId);
  if (!productor) return null;
  return {
    productor,
    usuarios: repo.listUsuarios(productorId),
    campos: repo.listCampos(productorId),
    lotes: repo.listLotes(productorId),
    campanias: repo.listCampanias(productorId),
    movimientos: repo.listMovimientos(productorId),
    hacienda: repo.listHacienda(productorId),
    sanidad: repo.listEventosSanitarios(productorId),
    margenes: margenPorLote(productorId),
    mensajes: repo.listRawMessages(productorId, 15),
  };
}

// --- Procesamiento asíncrono de mensajes de Meta ---------------------------

const colas = new Map<string, Promise<void>>();

/**
 * Serializa el trabajo por remitente.
 *
 * Meta manda cada mensaje en su propio request HTTP, así que "compré gasoil" y
 * el "sí" que lo confirma caen en callbacks independientes. Como el pipeline
 * espera al parser (que puede ser una llamada a OpenAI), sin serializar pueden
 * interleavearse y el "sí" no encontraría el pendiente todavía guardado
 * ("No tengo nada pendiente para confirmar", intermitente y sin error).
 */
function encolar(clave: string, fn: () => Promise<void>): void {
  const previo = colas.get(clave) ?? Promise.resolve();
  const siguiente = previo
    // setImmediate y no queueMicrotask: los microtasks drenan ANTES de que el
    // socket escriba, así que el pipeline correría antes de que salga el 200.
    .then(() => new Promise<void>((r) => setImmediate(r)))
    .then(fn)
    // El catch es obligatorio: Node ≥15 usa --unhandled-rejections=throw, así
    // que una rejection suelta en trabajo desprendido mata el servidor.
    .catch((e) => console.error('[wa] error procesando', clave, e));
  colas.set(clave, siguiente);
  void siguiente.finally(() => { if (colas.get(clave) === siguiente) colas.delete(clave); });
}

async function manejarEntrante(m: MensajeEntrante): Promise<void> {
  const sender = repo.getSenderByTelefono(m.from);

  if (!sender) {
    // Este log es a la vez el diagnóstico (muestra el formato exacto que mandó
    // Meta, que es como se detecta el tema del 9 argentino) y el workflow de alta.
    console.warn(`[wa] número no registrado: ${m.from} — vinculalo con: npm run link-phone -- <usuarioId> +${m.from}`);
    if (config.metaReplyToUnknown) {
      await enviarTexto(m.from, 'Hola 👋 Este número no está registrado en Parva. Pedile a tu administrador que te dé de alta.');
    }
    return;
  }

  if (m.tipo !== 'text') {
    await enviarTexto(m.from, 'Por ahora solo entiendo mensajes de texto 🙏 Las notas de voz llegan pronto.', m.waMessageId);
    return;
  }
  if (!m.texto.trim()) return;

  const r = await processMessage(sender, m.texto, m.waMessageId);
  if (!r) { console.log(`[wa] duplicado ignorado: ${m.waMessageId}`); return; }
  await enviarTexto(m.from, r.reply, m.waMessageId);
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://${req.headers.host}`);
  const path = url.pathname;
  const method = req.method ?? 'GET';

  try {
    // --- Estáticos / web app ---
    if (method === 'GET' && (path === '/' || path === '/index.html')) return await serveStatic(res, 'index.html');
    if (method === 'GET' && (path === '/styles.css' || path === '/app.js')) return await serveStatic(res, path.slice(1));
    if (method === 'GET' && path === '/healthz') return sendJson(res, 200, { ok: true });

    // --- API: productores y estado ---
    if (method === 'GET' && path === '/api/productores') {
      return sendJson(res, 200, repo.listProductores());
    }
    if (method === 'GET' && path === '/api/state') {
      const pid = Number(url.searchParams.get('productorId'));
      const state = buildState(pid);
      return state ? sendJson(res, 200, state) : sendJson(res, 404, { error: 'productor no encontrado' });
    }

    // --- API: export CSV ---
    if (method === 'GET' && path === '/api/export') {
      const pid = Number(url.searchParams.get('productorId'));
      const sheet = url.searchParams.get('sheet') ?? 'movimientos';
      const { filename, content } = exportCsv(pid, sheet);
      res.writeHead(200, {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': `attachment; filename="${filename}"`,
      });
      return res.end(content);
    }

    // --- Simulador de WhatsApp (lo usa la web app) ---
    if (method === 'POST' && path === '/api/whatsapp/sim') {
      const body = JSON.parse((await readBody(req)) || '{}') as { telefono?: string; texto?: string };
      if (!body.telefono || !body.texto) return sendJson(res, 400, { error: 'falta telefono o texto' });
      const sender = repo.getSenderByTelefono(body.telefono);
      if (!sender) return sendJson(res, 404, { error: 'teléfono no registrado' });
      const result = await processMessage(sender, body.texto);
      // El simulador no manda waMessageId, así que nunca dedupea; el guard está
      // para que el tipo sea honesto.
      if (!result) return sendJson(res, 409, { error: 'mensaje duplicado' });
      return sendJson(res, 200, result);
    }

    // --- Webhook Meta Cloud API: verificación (GET) ---
    if (method === 'GET' && path === '/webhook/whatsapp') {
      const mode = url.searchParams.get('hub.mode');
      const token = url.searchParams.get('hub.verify_token');
      const challenge = url.searchParams.get('hub.challenge');
      if (mode === 'subscribe' && token === config.metaVerifyToken) {
        console.log('[wa] ✓ handshake verificado — Meta guardó la callback URL');
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        return res.end(challenge ?? '');
      }
      console.warn(`[wa] ✗ handshake rechazado: verify_token no coincide (llegó "${token}", esperaba "${config.metaVerifyToken}")`);
      res.writeHead(403); return res.end('forbidden');
    }

    // --- Webhook Meta Cloud API: ingreso de mensajes (POST, forma real) ---
    if (method === 'POST' && path === '/webhook/whatsapp') {
      const raw = await readBodyBuffer(req);

      // La firma solo se exige en modo meta; en sim el endpoint queda abierto
      // igual que antes (NUNCA exponer sim a internet).
      if (modoMeta() && !verificarFirma(raw, req.headers['x-hub-signature-256'] as string | undefined)) {
        console.warn('[wa] firma inválida — descartado');
        res.writeHead(401); return res.end('firma inválida');
      }

      let payload: any;
      try { payload = JSON.parse(raw.toString('utf8') || '{}'); }
      catch { res.writeHead(400); return res.end('json inválido'); }

      const entrantes = extraerEntrantes(payload);

      if (modoMeta()) {
        // ACK primero: Meta reintenta durante días si el webhook tarda, y el
        // pipeline puede esperar a OpenAI. Recién después se procesa.
        sendJson(res, 200, { status: 'received', procesados: entrantes.length });
        for (const m of entrantes) encolar(m.from, () => manejarEntrante(m));
        return;
      }

      // --- sim: las respuestas van en el body (simulador web, curl local) ---
      const replies: unknown[] = [];
      for (const m of entrantes) {
        const sender = repo.getSenderByTelefono(m.from);
        if (sender && m.texto) {
          const r = await processMessage(sender, m.texto, m.waMessageId || null);
          if (r) replies.push(r);
        }
      }
      return sendJson(res, 200, { status: 'received', procesados: replies.length, replies });
    }

    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('No encontrado');
  } catch (err) {
    console.error('Error:', err);
    // Con el ack rápido es fácil tirar después de haber mandado los headers;
    // sin este guard el error real quedaría tapado por ERR_HTTP_HEADERS_SENT.
    if (!res.headersSent) sendJson(res, 500, { error: (err as Error).message });
  }
});

initSchema();
seedIfEmpty();
server.listen(config.port, () => {
  console.log(`\n🌾 Parva MVP corriendo en http://localhost:${config.port}`);
  parserActivo().then((m) => console.log(`   Parser activo: ${m}`));
  console.log(`   WhatsApp: modo ${config.whatsappMode}`);
  if (modoMeta()) {
    // La mayoría de las fallas de setup son una env var faltante, y sin este
    // aviso el server arrancaría en silencio y simplemente nunca contestaría.
    const faltan = faltaConfigMeta();
    if (faltan.length) {
      console.log(`   ⚠️  FALTA CONFIGURAR: ${faltan.join(', ')} — el bot no va a poder responder`);
    } else {
      console.log(`   ✓ credenciales de Meta cargadas (Graph ${config.metaGraphVersion})`);
    }
  }
  console.log(`   webhook (forma Meta): /webhook/whatsapp  ·  estado: /api/state?productorId=1\n`);
});
