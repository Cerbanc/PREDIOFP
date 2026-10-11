-- Migraciones de la base de la nube (D1). Se ejecutan UNA sola vez cada una, en la base que ya está en producción;
-- las bases nuevas ya salen completas de schema.sql. Hay que correrlas ANTES de publicar la versión del Worker que las usa.
-- Ejemplo: npx wrangler d1 execute predio-reportes --remote --file=nube/migraciones.sql
-- (si una sentencia dice «duplicate column name», esa ya estaba aplicada: se puede seguir con la siguiente)

-- Turnos en la web v2: quién cargó y quién cambió cada turno desde el celular.
-- Todas tienen valor por defecto: la sincronización con la PC (/api/sync) no las manda y no se rompe.
ALTER TABLE turnos ADD COLUMN creado_por TEXT NOT NULL DEFAULT '';
ALTER TABLE turnos ADD COLUMN modificado_por TEXT NOT NULL DEFAULT '';
ALTER TABLE turnos ADD COLUMN modificado_en INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fijos ADD COLUMN creado INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fijos ADD COLUMN creado_por TEXT NOT NULL DEFAULT '';
ALTER TABLE fijos ADD COLUMN modificado_por TEXT NOT NULL DEFAULT '';
ALTER TABLE fijos ADD COLUMN modificado_en INTEGER NOT NULL DEFAULT 0;
