# Reportes del Predio (Cloudflare)

Worker + base D1. La PC del local sube el resumen del día (`predio/nube.py`); esta página lo muestra en sólo lectura y deja bajar el Excel.
Las claves se guardan como huella SHA-256 en la tabla `config` (`hash_pc` para la PC, `hash_ver` para quien mira). Se publica con la integración de GitHub de Cloudflare (el `wrangler.toml` está en la raíz del repositorio y apunta a `nube/src/index.js`).

## Turnos

`/turnos` (misma clave). `src/turnos.js` es la única que decide los choques (Fútbol 7 contra los dos Fútbol 5, fijos, reprogramaciones) y anota cada cambio en `registro_turnos`. Las canchas, horarios y precios están en la tabla `canchas` (se cambian con una consulta SQL, ver `schema.sql`).

### Pantallas de `/turnos` (`src/pagina_turnos.html`, una sola página sin dependencias)

Pestañas (abajo en el celular, arriba en la compu): **Día** (una columna por cancha, con la raya roja de «ahora»), **Semana** (siete días con una casilla por horario: llena = ocupado, punteada = libre, rayada = lo ocupa Fútbol 7/5; tocar un día lo abre), **Buscar** (nombre o teléfono) y **Recordatorios** (turnos de mañana con teléfono, botón de WhatsApp con el mensaje ya escrito y «marcar enviado», que se recuerda en el celular por día). Se actualiza sola cada 30 s y al volver a la pestaña. Los horarios no están escritos en el código: salen de `ap`, `ci` y `dur` de cada cancha.

API (todo con la cookie de sesión): `GET /api/turnos?fecha=` · `GET /api/turnos/semana?desde=` (7 días) · `GET /api/turnos/buscar?q=` (últimos 30 días en adelante y fijos; sin acentos ni mayúsculas, teléfonos en cualquier formato, tolera un error de tipeo) · `POST /api/turnos` (crear, editar, cancelar, mover, fijos) · `GET /api/turnos/registro`.

Cada turno y cada fijo guardan quién lo cargó y quién lo cambió por última vez desde la web (`creado_por`, `modificado_por`, `modificado_en`; `''` si viene del programa de la PC, que no manda esas columnas). Si `modificado` es mayor que `modificado_en`, el último cambio lo hizo la PC. **Base ya publicada:** correr `nube/migraciones.sql` (ver el encabezado del archivo; si se publica el Worker antes, agrega las columnas solo).

Pruebas: `tests/test_nube_web.py` levanta esta nube en local (wrangler + base D1 simulada, `tests/nube_local.py`) y prueba el API, los choques entre celulares y las pantallas con un navegador real (`tests/e2e/turnos_web.js`); se saltea si falta wrangler (`/tmp/w/node_modules/.bin/wrangler` o `WRANGLER_PATH`) o node.

## Sincronización con la PC

`/api/sync` (clave de la PC, `src/sync.js`): la PC baja lo que cambió (`GET ?desde=`) y sube lo suyo (`POST`, cada registro con `base_mod`, la versión de la nube sobre la que se basó; si cambió en el medio se rechaza y la PC baja la nueva). Un turno `bloqueado` (en una cuenta o con pagos en la caja) no se puede cambiar desde la web. La parte del programa está en `predio/nube_turnos.py`.
