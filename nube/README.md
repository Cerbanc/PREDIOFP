# Reportes del Predio (Cloudflare)

Worker + base D1. La PC del local sube el resumen del día (`predio/nube.py`); esta página lo muestra en sólo lectura y deja bajar el Excel.
Las claves se guardan como huella SHA-256 en la tabla `config` (`hash_pc` para la PC, `hash_ver` para quien mira). Se publica con la integración de GitHub de Cloudflare (el `wrangler.toml` está en la raíz del repositorio y apunta a `nube/src/index.js`).

## Turnos

`/turnos` (misma clave). `src/turnos.js` es la única que decide los choques (Fútbol 7 contra los dos Fútbol 5, fijos, reprogramaciones) y anota cada cambio en `registro_turnos`. Las canchas, horarios y precios están en la tabla `canchas` (se cambian con una consulta SQL, ver `schema.sql`).

## Sincronización con la PC

`/api/sync` (clave de la PC, `src/sync.js`): la PC baja lo que cambió (`GET ?desde=`) y sube lo suyo (`POST`, cada registro con `base_mod`, la versión de la nube sobre la que se basó; si cambió en el medio se rechaza y la PC baja la nueva). Un turno `bloqueado` (en una cuenta o con pagos en la caja) no se puede cambiar desde la web. La parte del programa está en `predio/nube_turnos.py`.

## Reportes por período

La página de reportes tiene las pestañas Día, Semana (lunes a domingo), Mes y Fechas (rango libre, hasta 400 días). `src/reportes.js` arma el período ya sumado a partir de los resúmenes diarios de la tabla `dias`:

- `GET /api/rango?desde=&hasta=[&ant_desde=&ant_hasta=]` (sesión de la web, sólo lectura): día por día, totales, mejor y peor día, desglose por medio, categoría, destino y producto, fiado, cierres de caja y, si se pide, los totales del período anterior para comparar. No trae el listado de cambios (es lo más pesado).
- Un día **sin fila** en `dias` es un día que falta (la PC no lo subió): se marca "sin datos" y nunca se cuenta como cero. Un día con fila pero sin ventas ni caja es "sin movimiento".
- `GET /api/subidos?desde=` (clave de la PC): qué días tiene la nube y con qué huella. El programa (`predio/nube.py`, `rellenar`) lo usa para subir los últimos 90 días que falten o sean distintos (a los 5 minutos de abrir y cada ~6 horas, de a 8 por vez). La huella va dentro del resumen (`resumen.huella`), así que no hace falta cambiar la tabla.
