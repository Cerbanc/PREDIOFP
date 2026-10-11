-- Migraciones de la base de la nube (D1). Se corren UNA sola vez, en la base que ya está en producción;
-- las bases nuevas ya salen completas de schema.sql.
-- Ejemplo: npx wrangler d1 execute predio-reportes --remote --file=nube/migraciones.sql
-- Lo ideal es correrlas ANTES de publicar la versión nueva del Worker. Si se publicó primero no pasa nada: la web agrega sola
-- estas mismas columnas la primera vez que alguien usa los turnos (ver `asegurarColumnas` en src/turnos.js). En ese caso este archivo
-- dice «duplicate column name»: quiere decir que ya está todo hecho y no hace falta correrlo.

-- Turnos en la web v2: quién cargó y quién cambió cada turno desde el celular.
-- Todas tienen valor por defecto: la sincronización con la PC (/api/sync) no las manda y no se rompe.
ALTER TABLE turnos ADD COLUMN creado_por TEXT NOT NULL DEFAULT '';
ALTER TABLE turnos ADD COLUMN modificado_por TEXT NOT NULL DEFAULT '';
ALTER TABLE turnos ADD COLUMN modificado_en INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fijos ADD COLUMN creado INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fijos ADD COLUMN creado_por TEXT NOT NULL DEFAULT '';
ALTER TABLE fijos ADD COLUMN modificado_por TEXT NOT NULL DEFAULT '';
ALTER TABLE fijos ADD COLUMN modificado_en INTEGER NOT NULL DEFAULT 0;

-- Índice para saber rápido qué ocurrencias de turnos fijos ya están suspendidas o movidas (se puede correr de nuevo sin problema).
CREATE INDEX IF NOT EXISTS turnos_ocurrencia ON turnos (ocurrencia);
