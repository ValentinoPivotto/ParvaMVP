// Servidor HTTP con node:http (sin dependencias). Sirve la web app, la API del
// dashboard y el webhook de Meta Cloud API, que es por donde entran los
// mensajes reales de WhatsApp.
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { config, faltaConfigMeta } from '../config.ts';
import { initSchema } from '../repository/db.ts';
import { seedIfEmpty } from '../repository/seed.ts';
import * as repo from '../repository/repo.ts';
import { processMessage } from '../service/process.ts';
import { parserActivo } from '../service/parser.ts';
import { buildState } from '../service/dashboard.ts';
import { exportCsv } from '../service/export.ts';
import { verificarFirma, extraerEntrantes, enviarTexto, type MensajeEntrante } from './whatsapp.ts';

const WEB_DIR = fileURLToPath(new URL('../../frontend/', import.meta.url));
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

// --- Procesamiento asíncrono de mensajes de Meta ---------------------------

const colas = new Map<string, Promise<void>>();

/**
 * Serializa el trabajo por remitente.
 *
 * Meta manda cada mensaje en su propio request HTTP, así que "compré gasoil" y
 * el "sí" que lo confirma caen en callbacks independientes. Como el pipeline
 * espera al parser (que puede ser una llamada al modelo), sin serializar pueden
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
      await enviarTexto(m.from, 'Hola 👋 Este número no está registrado en Parva. Pedile a tu administrador que te dé de alta.', undefined, m.phoneNumberId);
    }
    return;
  }

  if (m.tipo !== 'text') {
    await enviarTexto(m.from, 'Por ahora solo entiendo mensajes de texto 🙏 Las notas de voz llegan pronto.', m.waMessageId, m.phoneNumberId);
    return;
  }
  if (!m.texto.trim()) return;

  // `|| null` y no el id tal cual: extraerEntrantes usa '' cuando el envelope
  // no trae `msg.id` (un curl de prueba), y el índice único de wa_message_id
  // trata a '' como un valor — el segundo mensaje sin id se dedupearía solo.
  const r = await processMessage(sender, m.texto, m.waMessageId || null);
  if (!r) { console.log(`[wa] duplicado ignorado: ${m.waMessageId}`); return; }
  await enviarTexto(m.from, r.reply, m.waMessageId, m.phoneNumberId);
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

    // --- Webhook Meta Cloud API: ingreso de mensajes (POST) ---
    if (method === 'POST' && path === '/webhook/whatsapp') {
      const raw = await readBodyBuffer(req);

      // Sin firma válida no se procesa nada: el webhook está expuesto a
      // internet por el túnel y es la única puerta de entrada al pipeline.
      if (!verificarFirma(raw, req.headers['x-hub-signature-256'] as string | undefined)) {
        console.warn('[wa] firma inválida — descartado');
        res.writeHead(401); return res.end('firma inválida');
      }

      let payload: any;
      try { payload = JSON.parse(raw.toString('utf8') || '{}'); }
      catch { res.writeHead(400); return res.end('json inválido'); }

      const entrantes = extraerEntrantes(payload);

      // ACK primero: Meta reintenta durante días si el webhook tarda, y el
      // pipeline puede esperar al modelo. Recién después se procesa.
      sendJson(res, 200, { status: 'received', procesados: entrantes.length });
      for (const m of entrantes) {
        // Qué número propio recibió el mensaje: es el dato que falta cuando la
        // WABA tiene el de test y el propio y uno de los dos "no contesta".
        console.log(`[wa] ← ${m.from} → nuestro número ${m.phoneNumberId || '(sin metadata)'}${m.wabaId ? ` · WABA ${m.wabaId}` : ''}`);
        encolar(m.from, () => manejarEntrante(m));
      }
      return;
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
  // La mayoría de las fallas de setup son una env var faltante, y sin este
  // aviso el server arrancaría en silencio y simplemente nunca contestaría.
  const faltan = faltaConfigMeta();
  if (faltan.length) {
    console.log(`   ⚠️  FALTA CONFIGURAR: ${faltan.join(', ')} — el bot no va a poder responder`);
  } else {
    console.log(`   ✓ credenciales de Meta cargadas (Graph ${config.metaGraphVersion})`);
  }
  console.log(`   webhook de Meta: /webhook/whatsapp  ·  estado: /api/state?productorId=1\n`);
});
