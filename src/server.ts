// Servidor HTTP con node:http (sin dependencias). Sirve la web app, la API del
// dashboard, el endpoint del simulador de WhatsApp y el webhook con la forma
// real de Meta Cloud API (mockeado: no requiere cuenta de Meta).
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { config } from './config.ts';
import { initSchema } from './db.ts';
import { seedIfEmpty } from './seed.ts';
import * as repo from './repo.ts';
import { processMessage } from './pipeline/process.ts';
import { parserActivo } from './pipeline/parser.ts';
import { margenPorLote } from './services/margin.ts';
import { exportCsv } from './services/export.ts';

const WEB_DIR = fileURLToPath(new URL('../web/', import.meta.url));
const VERIFY_TOKEN = 'parva-dev'; // token del handshake del webhook (mock)

function sendJson(res: ServerResponse, code: number, data: unknown): void {
  const body = JSON.stringify(data);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(body);
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => { data += c; });
    req.on('end', () => resolve(data));
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
      return sendJson(res, 200, result);
    }

    // --- Webhook Meta Cloud API: verificación (GET) ---
    if (method === 'GET' && path === '/webhook/whatsapp') {
      const mode = url.searchParams.get('hub.mode');
      const token = url.searchParams.get('hub.verify_token');
      const challenge = url.searchParams.get('hub.challenge');
      if (mode === 'subscribe' && token === VERIFY_TOKEN) {
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        return res.end(challenge ?? '');
      }
      res.writeHead(403); return res.end('forbidden');
    }

    // --- Webhook Meta Cloud API: ingreso de mensajes (POST, forma real) ---
    if (method === 'POST' && path === '/webhook/whatsapp') {
      const payload = JSON.parse((await readBody(req)) || '{}');
      const replies: unknown[] = [];
      const entries = payload.entry ?? [];
      for (const entry of entries) {
        for (const change of entry.changes ?? []) {
          for (const msg of change.value?.messages ?? []) {
            const from: string = msg.from ?? '';
            const telefono = from.startsWith('+') ? from : '+' + from;
            const texto: string = msg.text?.body ?? '';
            const sender = repo.getSenderByTelefono(telefono);
            if (sender && texto) replies.push(await processMessage(sender, texto));
          }
        }
      }
      return sendJson(res, 200, { status: 'received', procesados: replies.length, replies });
    }

    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('No encontrado');
  } catch (err) {
    console.error('Error:', err);
    sendJson(res, 500, { error: (err as Error).message });
  }
});

initSchema();
seedIfEmpty();
server.listen(config.port, () => {
  console.log(`\n🌾 Parva MVP corriendo en http://localhost:${config.port}`);
  parserActivo().then((m) => console.log(`   Parser activo: ${m}`));
  console.log(`   webhook (forma Meta): /webhook/whatsapp  ·  estado: /api/state?productorId=1\n`);
});
