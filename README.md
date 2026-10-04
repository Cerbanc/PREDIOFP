# Caja del Predio

Punto de venta **100 % sin internet** para un predio deportivo (kiosco, pádel, buffet, mesas y cuentas abiertas). Python + SQLite en la PC del mostrador, pantalla HTML en una ventana propia, instalador para Windows 10.

| Carpeta | Qué hay |
|---|---|
| `predio/` | El programa (Python, sólo biblioteca estándar + `openpyxl`) y la pantalla (`predio/web/index.html`) |
| `tests/` | 60+ pruebas con pytest y dos pruebas de punta a punta con un navegador real |
| `packaging/` | PyInstaller, instalador Inno Setup y prueba del ejecutable |
| `.github/workflows/windows.yml` | Arma, prueba (en Windows) y publica el instalador |
| `docs/GUIA.md` | Guía para quien lo usa (en español, sin tecnicismos) |
| `prototipo/` | El prototipo HTML original (guarda en el navegador). Quedó como referencia |

## Cómo funciona

```
 ventana (Edge/Chrome en modo app)         Python (127.0.0.1:8765)                  C:\Predio
┌────────────────────────────────┐  JSON  ┌───────────────────────────┐   ┌────────────────────────────┐
│ predio/web/index.html          │──────▶│ servidor.py               │   │ datos\predio.db  (SQLite)  │
│ DB en memoria + reglas de      │◀──────│  └ almacen.py  guarda     │──▶│ dias\AAAA-MM-DD\*.xlsx/txt │
│ negocio (stock, caja, fiado)   │        │  └ auditoria.py anota     │   │ copias\  imagenes\  ...    │
└────────────────────────────────┘        │  └ diario.py / copias.py  │   └────────────────────────────┘
                                          └───────────────────────────┘
```

- **Las reglas del negocio viven en la pantalla** (como en el prototipo, para poder seguir ajustándolas): descuentos, stock, caja, fiado, división de cuentas. La pantalla trabaja con los datos en memoria y, **al terminar cada operación (`tx()`), manda al programa sólo lo que cambió**.
- **El guardado es sincrónico**: `tx()` no devuelve «ok» hasta que SQLite confirmó (`COMMIT`, `synchronous=FULL`). Si el programa rechaza el guardado (disco lleno, otra ventana más nueva, programa cerrado), `tx()` hace **rollback en pantalla** y la operación se cancela: nada queda a medias, ni en memoria ni en disco.
- **Una transacción SQLite por operación**: todos los cambios de un guardado entran juntos o ninguno (`BEGIN IMMEDIATE … COMMIT/ROLLBACK`).
- **Control de versión (`rev`)**: cada guardado sube un número. Una ventana que quedó con una versión vieja recibe `409`, muestra «Hay que recargar» y **no puede pisar** datos más nuevos.

### Base de datos (`predio/db.py`)

- `docs(coleccion, id, seq, rev, ts, dia, data JSON)`: todos los datos del negocio, un registro por fila (productos, ventas, mesas, cajas, clientes…). `seq` conserva el orden de las listas.
- `auditoria`: **registro de cambios automático**, sólo se agrega (dos *triggers* impiden `UPDATE` y `DELETE`). Lo escribe `auditoria.py` comparando el antes y el después de cada guardado, sin depender de qué pantalla lo hizo: ítems sacados de una cuenta, cuentas vaciadas sin cobrar, movimientos de caja, ajustes de stock sin movimiento, cambios de precio, turnos agendados/reprogramados/cancelados/borrados, tickets anulados o borrados, cambio de código… Lo borrado queda entero en `antes`. El código de administración nunca se escribe en el registro.
- `v_*`: vistas de sólo lectura con columnas en español (`v_ventas`, `v_venta_items`, `v_productos`, `v_libro_caja`, `v_cajas`, `v_cambios`…) para mirar la base desde DB Browser for SQLite, Excel, Power BI o un panel remoto.
- WAL + `synchronous=FULL` + verificación al abrir, copias con la API de backup de SQLite verificadas con `quick_check`, y recuperación automática desde la última copia sana si la base estuviera dañada.
- `user_version` y migraciones en `db.migrar()` (antes de migrar una base existente se hace una copia `seguridad_antes_de_migrar`).

### Carpeta por día (`predio/diario.py`)

Cada guardado marca los días tocados; un hilo de fondo (espera 2,5 s sin novedades) regenera `dias/AAAA-MM-DD/AAAA-MM-DD_resumen.xlsx` (Resumen, Ventas, Detalle, Libro de caja, Movimientos de caja, Stock, Turnos, Cambios) y `…_registro.txt`. Se escribe a un temporal y se reemplaza; si el Excel está abierto en Windows se reintenta solo.

### Rendimiento con los años

La pantalla sólo carga los últimos `ventana_dias` (35) de `ventas`, `caja`, `movimientos`, `movCaja` y `log`; lo anterior se pide a `/api/historia` cuando se mira un reporte o ticket viejo. Con un año de datos simulados (36 500 tickets, 146 000 líneas de registro, base de 140 MB): abre en 0,4 s y cada operación tarda ~70 ms (sin ventana eran 17 s y ~0,9 s).

### Seguridad

- El servidor escucha **sólo en 127.0.0.1**, valida el `Host`, el `Origin` y exige un token por sesión (`X-Predio-Token`, embebido en la página) en toda la API: otra página abierta en el navegador no puede leer ni escribir datos.
- Cabeceras `Content-Security-Policy: default-src 'self'` (la pantalla no hace ningún pedido a internet), `X-Frame-Options: DENY`, `nosniff`.
- Las imágenes se guardan como archivos con nombre por contenido (`sha256`) y sólo se sirven con ese patrón (sin recorridos de carpetas, sin SVG).
- Los datos no se suben a ningún lado. El código de administración es una traba contra errores, no seguridad contra alguien con acceso a la PC.

### API local (todo con token)

`GET /api/estado` · `POST /api/guardar` · `GET /api/historia` · `GET /api/auditoria[/{id}]` · `GET /api/dia/{fecha}` · `GET|POST /api/copias[/crear|/restaurar]` · `POST /api/borrar-historial` · `GET /api/planilla/{plantilla|exportar}.xlsx` · `POST /api/planilla/leer` · `POST /api/imagen-desde-link` · `POST /api/abrir-carpeta` · `GET /api/salud` · `POST /api/cerrar`. `/api/dia` y `/api/auditoria` son de sólo lectura y devuelven JSON listo para un panel remoto.

## Desarrollo

```bash
pip install -r requirements-dev.txt
python -m predio --datos ./datos_de_prueba --sin-ventana     # abre http://127.0.0.1:8765 en el navegador
python -m predio --datos ./datos_de_prueba --demo            # con datos de ejemplo (carpeta aparte)
python -m pytest tests -q                                     # pruebas (las de navegador se saltan si no hay node + playwright)
```

Opciones: `--datos`, `--demo`, `--sin-ventana`, `--puerto`. `configuracion.json` (en la carpeta de datos): `puerto`, `copias_conservar_dias`, `ventana_completa`, `ventana_dias`. Variable `PREDIO_DATOS` para elegir la carpeta.

La pantalla (`predio/web/index.html`) sigue funcionando abierta como archivo suelto (guarda en el navegador): es útil para probar cambios de interfaz sin levantar el programa.

## Instalador de Windows

El flujo `windows.yml` corre en `windows-latest`: pruebas → `pyinstaller packaging/predio.spec` (carpeta, no `--onefile`) → `packaging/probar_exe.py` arranca el ejecutable armado, pide la página y el estado y lo cierra → Inno Setup genera `Instalar-Caja-del-Predio-X.Y.Z.exe` y un `.zip` portable → artefacto de la corrida (y *Release* si se sube una etiqueta `vX.Y.Z`). Para armarlo a mano en Windows: `packaging\build_windows.bat`.

Los datos van en `C:\Predio` (o `PREDIO_DATOS`) y **no** en Documentos, porque ahí OneDrive suele sincronizar y no debe sincronizar una base abierta. El instalador nunca los toca.

## Actualizaciones

`predio/actualizador.py` consulta `releases/latest` del repositorio indicado en `configuracion.json` (`actualizaciones_repo`), compara versiones, baja el instalador (verifica tamaño y SHA-256 de GitHub, sólo hosts de GitHub), hace una copia, cierra el programa y recién ahí ejecuta el instalador en modo silencioso (que vuelve a abrir el programa). El instalador es por usuario (sin administrador). Se publica una versión subiendo la etiqueta `vX.Y.Z` (debe coincidir con `predio/__init__.py`). **Si el repositorio es privado**, la consulta anónima no ve las versiones: hay que hacerlo público o crear un repositorio público sólo para versiones y definir el secreto `RELEASES_TOKEN` y la variable `RELEASES_REPO` (el flujo publica ahí también) y apuntar `actualizaciones_repo` a ese repositorio.

## Qué falta (a propósito)

Turnos desde el celular (planilla en la nube), panel remoto de sólo lectura, espejo en Google Sheets, lectura real de remitos con IA, impresora de tickets. El diseño ya los contempla: la API de lectura, las vistas `v_*` y el número de revisión (`rev`) sirven para sincronizar.
