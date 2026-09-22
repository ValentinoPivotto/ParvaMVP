CREATE TABLE audit_log (
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
CREATE TABLE campania (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  cultivo_actividad TEXT,
  fecha_inicio TEXT,
  fecha_fin TEXT
);
CREATE TABLE campo (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  hectareas REAL
);
CREATE TABLE evento_hacienda (
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
CREATE TABLE evento_sanitario (
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
CREATE TABLE hacienda (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  categoria TEXT NOT NULL,                        -- ternero | vaca | novillo | toro | vaquillona
  cantidad INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE lote (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  campo_id INTEGER REFERENCES campo(id),
  nombre TEXT NOT NULL,                           -- "Lote 4"
  numero TEXT,                                    -- "4"
  hectareas REAL,
  uso_actual TEXT
);
CREATE TABLE movimiento (
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
CREATE TABLE productor (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre TEXT NOT NULL,
  pais TEXT NOT NULL,
  tipo_campo TEXT NOT NULL,                       -- agricola | ganadero | mixto
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE raw_message (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  usuario_id INTEGER REFERENCES usuario(id),
  texto TEXT NOT NULL,
  intent TEXT,
  record_type TEXT,
  parsed_json TEXT,
  confidence REAL,
  estado TEXT NOT NULL DEFAULT 'pending',         -- pending | confirmed | discarded
  wa_message_id TEXT,                             -- id de Meta (wamid.…); NULL si el envelope no lo trae
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE usuario (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  productor_id INTEGER NOT NULL REFERENCES productor(id),
  nombre TEXT NOT NULL,
  telefono TEXT NOT NULL UNIQUE,                  -- clave de match del webhook
  rol TEXT NOT NULL                               -- owner | gestor_campo
);
CREATE UNIQUE INDEX ux_raw_message_wa_id ON raw_message(wa_message_id);
