CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dias (
  dia TEXT PRIMARY KEY,
  resumen TEXT NOT NULL,
  xlsx TEXT,
  subido INTEGER NOT NULL
);

-- Turnos (fuente única: la nube)
CREATE TABLE IF NOT EXISTS canchas (
  id TEXT PRIMARY KEY, clave TEXT NOT NULL, nombre TEXT NOT NULL, deporte TEXT NOT NULL,
  dur INTEGER NOT NULL, ap INTEGER NOT NULL, ci INTEGER NOT NULL, precio INTEGER NOT NULL, precio_fijo INTEGER NOT NULL, orden INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fijos (
  id TEXT PRIMARY KEY, cancha_id TEXT NOT NULL, dow INTEGER NOT NULL, ini INTEGER NOT NULL,
  cliente TEXT NOT NULL DEFAULT '', tel TEXT NOT NULL DEFAULT '', nota TEXT NOT NULL DEFAULT '',
  desde TEXT NOT NULL, hasta TEXT, modificado INTEGER NOT NULL DEFAULT 0,
  -- quién cargó / cambió desde la web ('' = viene del programa de la PC, que no manda estas columnas)
  creado INTEGER NOT NULL DEFAULT 0, creado_por TEXT NOT NULL DEFAULT '', modificado_por TEXT NOT NULL DEFAULT '', modificado_en INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS turnos (
  id TEXT PRIMARY KEY, cancha_id TEXT NOT NULL, fecha TEXT NOT NULL, ini INTEGER NOT NULL, fin INTEGER NOT NULL,
  cliente TEXT NOT NULL DEFAULT '', tel TEXT NOT NULL DEFAULT '', nota TEXT NOT NULL DEFAULT '',
  precio INTEGER NOT NULL, sena INTEGER NOT NULL DEFAULT 0, estado TEXT NOT NULL DEFAULT 'reservado',
  tipo_precio TEXT NOT NULL DEFAULT 'comun', fijo_id TEXT, ocurrencia TEXT, creado INTEGER NOT NULL, modificado INTEGER NOT NULL,
  bloqueado INTEGER NOT NULL DEFAULT 0,         -- 1 = ya está en una cuenta o cobrado en la caja: desde la web no se toca
  -- quién cargó / cambió desde la web ('' = viene del programa de la PC, que no manda estas columnas);
  -- modificado_en = cuándo fue ese último cambio de la web (si `modificado` es mayor, después tocó la PC)
  creado_por TEXT NOT NULL DEFAULT '', modificado_por TEXT NOT NULL DEFAULT '', modificado_en INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS turnos_fecha ON turnos (fecha, estado);
CREATE TABLE IF NOT EXISTS registro_turnos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, quien TEXT NOT NULL, accion TEXT NOT NULL, detalle TEXT NOT NULL
);
INSERT OR IGNORE INTO canchas VALUES
 ('f5a','f5a','Fútbol 5 · Cancha 1','futbol',60,600,1440,28000,24000,1),
 ('f5b','f5b','Fútbol 5 · Cancha 2','futbol',60,600,1440,28000,24000,2),
 ('f7','f7','Fútbol 7 · Cancha completa','futbol',60,600,1440,48000,42000,3),
 ('padelT','padelT','Pádel · Techada','padel',90,480,1440,26000,22000,4),
 ('padelA','padelA','Pádel · Abierta','padel',90,480,1440,22000,19000,5);
