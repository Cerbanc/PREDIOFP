# Reportes del Predio (Cloudflare)

Worker + base D1. La PC del local sube el resumen del día (`predio/nube.py`); esta página lo muestra en sólo lectura y deja bajar el Excel.
Las claves se guardan como huella SHA-256 en la tabla `config` (`hash_pc` para la PC, `hash_ver` para quien mira). Se publica con la integración de GitHub de Cloudflare (el `wrangler.toml` está en la raíz del repositorio y apunta a `nube/src/index.js`).

## Turnos

`/turnos` (misma clave). `src/turnos.js` es la única que decide los choques (Fútbol 7 contra los dos Fútbol 5, fijos, reprogramaciones) y anota cada cambio en `registro_turnos`. Las canchas, horarios y precios están en la tabla `canchas` (se cambian con una consulta SQL, ver `schema.sql`). Los horarios son los de la planilla de papel (fútbol 14:00 a 23:00 en turnos de 1 hora; pádel 10:00 a 23:30 en turnos de 90 minutos); la PC los manda sola al sincronizar y `migraciones.sql` tiene los `UPDATE` equivalentes (no se ejecutan solos).

### Pantallas de `/turnos` (`src/pagina_turnos.html`, una sola página sin dependencias)

Pestañas (abajo en el celular, arriba en la compu): **Día** (una columna por cancha, con la raya roja de «ahora»), **Semana** (siete días con una casilla por horario: llena = ocupado, punteada = libre, rayada = lo ocupa Fútbol 7/5; tocar un día lo abre), **Buscar** (nombre o teléfono) y **Recordatorios** (turnos de mañana con teléfono, botón de WhatsApp con el mensaje ya escrito y «marcar enviado», que se recuerda en el celular por día). Se actualiza sola cada 30 s y al volver a la pestaña. Los horarios no están escritos en el código: salen de `ap`, `ci` y `dur` de cada cancha.

API (todo con la cookie de sesión): `GET /api/turnos?fecha=` · `GET /api/turnos/semana?desde=` (7 días) · `GET /api/turnos/buscar?q=` (últimos 30 días en adelante y fijos; sin acentos ni mayúsculas, teléfonos en cualquier formato, tolera un error de tipeo) · `POST /api/turnos` (crear, editar, cancelar, mover, fijos) · `GET /api/turnos/registro`.

Cada turno y cada fijo guardan quién lo cargó y quién lo cambió por última vez desde la web (`creado_por`, `modificado_por`, `modificado_en`; `''` si viene del programa de la PC, que no manda esas columnas). Si `modificado` es mayor que `modificado_en`, el último cambio lo hizo la PC. **Base ya publicada:** correr `nube/migraciones.sql` (ver el encabezado del archivo; si se publica el Worker antes, agrega las columnas solo).

Pruebas: `tests/test_nube_web.py` levanta esta nube en local (wrangler + base D1 simulada, `tests/nube_local.py`) y prueba el API, los choques entre celulares y las pantallas con un navegador real (`tests/e2e/turnos_web.js`); se saltea si falta wrangler (`/tmp/w/node_modules/.bin/wrangler` o `WRANGLER_PATH`) o node.

## Sincronización con la PC

`/api/sync` (clave de la PC, `src/sync.js`): la PC baja lo que cambió (`GET ?desde=`) y sube lo suyo (`POST`, cada registro con `base_mod`, la versión de la nube sobre la que se basó; si cambió en el medio se rechaza y la PC baja la nueva). Un turno `bloqueado` (en una cuenta o con pagos en la caja) no se puede cambiar desde la web. La parte del programa está en `predio/nube_turnos.py`.

## Reportes por período

La página de reportes tiene las pestañas Día, Semana (lunes a domingo), Mes y Fechas (rango libre, hasta 400 días). `src/reportes.js` arma el período ya sumado a partir de los resúmenes diarios de la tabla `dias`:

- `GET /api/rango?desde=&hasta=[&ant_desde=&ant_hasta=]` (sesión de la web, sólo lectura): día por día, totales, mejor y peor día, desglose por medio, categoría, destino y producto, fiado, cierres de caja y, si se pide, los totales del período anterior para comparar. No trae el listado de cambios (es lo más pesado).
- Un día **sin fila** en `dias` es un día que falta (la PC no lo subió): se marca "sin datos" y nunca se cuenta como cero. Un día con fila pero sin ventas ni caja es "sin movimiento".
- `GET /api/subidos?desde=` (clave de la PC): qué días tiene la nube y con qué huella. El programa (`predio/nube.py`, `rellenar`) lo usa para subir los últimos 90 días que falten o sean distintos (a los 5 minutos de abrir y cada ~6 horas, de a 8 por vez). La huella va dentro del resumen (`resumen.huella`), así que no hace falta cambiar la tabla.

## Web instalable en el celular (PWA)

Reportes y Turnos se pueden instalar como app (ícono del Predio en la pantalla de inicio, sin barra del navegador). `src/pwa.js` sirve, **sin clave** (van antes del control de sesión en `src/index.js`, y no llevan ningún dato del negocio): `/manifest.webmanifest`, `/sw.js` (service worker mínimo: no guarda nada; sólo muestra «Sin conexión» si no hay internet) y los íconos (`/icono-192.png`, `/icono-512.png`, `/icono-maskable-512.png`, `/apple-touch-icon.png`, embebidos en base64 en `src/iconos.js`).

El cartel «Instalar como app» es un script inline idéntico en las dos páginas (entre los comentarios `PWA-INICIO` y `PWA-FIN`, en el `<head>` y al final). Aparece sólo en celular o tablet táctil, después de entrar con la clave, y nunca si la web ya se abrió desde el ícono o si se cerró con la «x» (se recuerda en el celular). En Android/Chrome tiene el botón «Instalar»; en iPhone no existe ese botón, así que explica los pasos (Compartir, Agregar a pantalla de inicio). En iPhone la app instalada guarda sus datos aparte de Safari: hay que escribir la clave una vez dentro de la app.

Para cambiar el logo: reemplazar `tools/isotipo.png` y correr `python nube/tools/hacer_iconos.py` (necesita Pillow, sólo para este script); regenera `src/iconos.js`. Pruebas: `tests/test_pwa.py` (levanta una nube local con wrangler y un celular simulado con Playwright; se saltea si faltan).
