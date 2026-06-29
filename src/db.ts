// Capa de base de datos: SQLite nativo de Node (node:sqlite), sin dependencias.
// En producción se cambiaría por Postgres/Supabase; el repositorio (repo.ts)
// aísla el resto del código de este detalle.
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { config } from './config.ts';

mkdirSync(dirname(config.dbPath), { recursive: true });

export const db = new DatabaseSync(config.dbPath);
db.exec('PRAGMA foreign_keys = ON;');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS productor (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre TEXT NOT NULL,
  pais TEXT NOT NULL,
  tipo_campo TEXT NOT NULL,                       -- agricola | ganadero | mixto
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS usuario (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  telefono TEXT NOT NULL UNIQUE,                  -- clave de match del webhook
  rol TEXT NOT NULL                               -- owner | gestor_campo
);
CREATE TABLE IF NOT EXISTS campo (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  hectareas REAL
);
CREATE TABLE IF NOT EXISTS lote (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  nombre TEXT NOT NULL,                           -- "Lote 4"
  numero TEXT,                                    -- "4"
  hectareas REAL,
  uso_actual TEXT
);
CREATE TABLE IF NOT EXISTS campania (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  cultivo_actividad TEXT,
  fecha_inicio TEXT,
  fecha_fin TEXT
);
CREATE TABLE IF NOT EXISTS movimiento (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  tipo TEXT NOT NULL,                             -- insumo | labor | gasto | venta
  lote_id INTEGER REFERENCES lote(id),
  campania_id INTEGER REFERENCES campania(id),
  fecha TEXT NOT NULL,
  producto TEXT,
  cantidad REAL,
  unidad TEXT,
  monto REAL,
  moneda TEXT DEFAULT 'ARS',
  categoria TEXT,
  descripcion TEXT,
  origen TEXT NOT NULL DEFAULT 'web',             -- bot | web | seed
  created_by INTEGER REFERENCES usuario(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS hacienda (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  categoria TEXT NOT NULL,                        -- ternero | vaca | novillo | toro | vaquillona
  cantidad INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evento_hacienda (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  tipo TEXT NOT NULL,                             -- nacimiento | muerte | compra | venta | traslado
  categoria TEXT NOT NULL,
  cantidad INTEGER NOT NULL,
  monto REAL,
  fecha TEXT NOT NULL,
  origen TEXT NOT NULL DEFAULT 'web',
  created_by INTEGER REFERENCES usuario(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evento_sanitario (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  producto TEXT,
  categoria TEXT,
  cantidad INTEGER,
  fecha TEXT NOT NULL,
  origen TEXT NOT NULL DEFAULT 'web',
  created_by INTEGER REFERENCES usuario(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS raw_message (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  usuario_id INTEGER REFERENCES usuario(id),
  texto TEXT NOT NULL,
  intent TEXT,
  record_type TEXT,
  parsed_json TEXT,
  confidence REAL,
  estado TEXT NOT NULL DEFAULT 'pending',         -- pending | confirmed | discarded
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  entidad TEXT NOT NULL,
  entidad_id INTEGER,
  accion TEXT NOT NULL,
  usuario_id INTEGER,
  origen TEXT,
  detalle TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
`;

export function initSchema(): void {
  db.exec(SCHEMA);
}

export function isEmpty(): boolean {
  initSchema();
  const row = db.prepare('SELECT COUNT(*) AS n FROM productor').get() as { n: number };
  return row.n === 0;
}

export function dropAll(): void {
  const tablas = [
    'audit_log', 'raw_message', 'evento_sanitario', 'evento_hacienda',
    'hacienda', 'movimiento', 'campania', 'lote', 'campo', 'usuario', 'productor',
  ];
  for (const t of tablas) db.exec(`DROP TABLE IF EXISTS ${t};`);
  initSchema();
}

// Helper: convierte lastInsertRowid (number | bigint) a number.
export function lastId(info: { lastInsertRowid: number | bigint }): number {
  return Number(info.lastInsertRowid);
}
