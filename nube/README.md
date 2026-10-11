# Reportes del Predio (Cloudflare)

Worker + base D1. La PC del local sube el resumen del día (`predio/nube.py`); esta página lo muestra en sólo lectura y deja bajar el Excel.
Las claves se guardan como huella SHA-256 en la tabla `config` (`hash_pc` para la PC, `hash_ver` para quien mira). Se publica con la integración de GitHub de Cloudflare (el `wrangler.toml` está en la raíz del repositorio y apunta a `nube/src/index.js`).

## Turnos

`/turnos` (misma clave). `src/turnos.js` es la única que decide los choques (Fútbol 7 contra los dos Fútbol 5, fijos, reprogramaciones) y anota cada cambio en `registro_turnos`. Las canchas, horarios y precios están en la tabla `canchas` (se cambian con una consulta SQL, ver `schema.sql`).
