# Caja del Predio · Guía para usarla

Punto de venta para el predio (kiosco, pádel y buffet) que **funciona sin internet**. Se instala en la PC del mostrador y todos la usan desde ahí. Los datos quedan en esa PC.

## 1. Instalarla

1. Bajá el instalador. Mientras no haya una versión publicada, está en GitHub: pestaña **Actions** → la última corrida de **Programa para Windows** → abajo, en **Artifacts**, **Caja-del-Predio-Windows** (es un .zip: adentro está `Instalar-Caja-del-Predio-…exe`).
2. Ejecutalo y seguí los pasos. Si Windows dice «Windows protegió su PC», tocá **Más información → Ejecutar de todas formas** (pasa porque el instalador no tiene firma digital paga).
3. Quedan dos íconos en el escritorio:
   - **Caja del Predio**: el programa de verdad.
   - **Caja del Predio (demostración)**: abre una copia aparte con datos de ejemplo, para mostrarla o practicar sin tocar nada real.
4. Al abrirla por primera vez se crea la carpeta **`C:\Predio`**. Ahí vive todo (ver el punto 5).

Para **actualizarla**: el programa avisa solo (si hay internet) cuando hay una versión nueva, con un cartel arriba: **Ver qué cambió y actualizar**. Se baja el instalador completo (unos 20 MB) **una sola vez y sólo si lo pedís**, hace una copia de seguridad, se cierra y se vuelve a abrir solo. También está *Admin → Datos y seguridad → Buscar actualización*. Los datos de `C:\Predio` no se tocan nunca; si la base necesita cambios, el programa hace otra copia y los hace solo. No hace falta ser administrador de Windows. Las versiones se publican en GitHub como v1.1.0, v1.2.0…

## 2. El primer día: cargar productos y stock

1. Entrá a **Admin** (código de fábrica **1234**; cambialo enseguida en *Admin → Datos y seguridad*). La primera pestaña es la **Planilla (productos y stock)**.
2. Cargá los productos de una de estas formas, o combinándolas:
   - **Escribir en la tabla**: una fila por producto. Con *Enter* bajás, con las flechas te movés, con *Tab* pasás a la celda de al lado.
   - **Pegar desde Excel**: copiás las celdas en Excel y las pegás con *Ctrl+V* en la tabla. Funciona con o sin la fila de títulos.
   - **Cargar archivo de Excel**: botón *Cargar archivo de Excel…*. Si no tenés una planilla armada, *Bajar plantilla vacía* te da una lista para completar (en la PC o en el celular). Esa misma plantilla está en este repositorio, en [`docs/plantilla_productos_y_stock.xlsx`](plantilla_productos_y_stock.xlsx): se puede ir completando **desde ya**, antes de instalar nada, y después cargarla de una sola vez.
3. Qué va en cada columna:
   | Columna | Para qué sirve |
   |---|---|
   | **Producto** | El nombre como lo ve quien cobra. |
   | **Categoría** | Kiosco, Pádel, Buffet… Si escribís una nueva, se crea sola. |
   | **Tipo** | *Producto* (lleva su stock), *Receta* (se arma con insumos, ej. pancho), *Servicio* (sin stock, ej. café), *Insumo* (sólo se usa en recetas, ej. salchicha). |
   | **Precio / Costo** | Precio de venta y lo que te cuesta. El costo sólo lo ve el administrador y sirve para vender «al costo» a empleados. |
   | **Stock hoy** | Cuántas unidades hay **hoy**. |
   | **Mínimo** | El sistema avisa cuando quedan tantas o menos. |
   | **Cómo viene / Unid. por paquete** | Ej. *Caja x 24* y *24*: así, al cargar un remito, «4 cajas» suma 96 unidades. |
   | **Receta** | Para el tipo Receta: `1 Salchicha + 1 Pan de pancho`. |
4. Lo que está **en rojo** hay que corregirlo (pasá el mouse para ver qué pasa) y el botón **Guardar** se habilita cuando no queda nada en rojo. Nada se guarda hasta que lo apretás, y se guarda **todo junto o nada**.
5. Cuando el producto ya existe, la planilla lo **actualiza**; si cambiás el stock, queda registrado como un **ajuste** en el historial (con fecha y quién).

> Después, para agregar un producto suelto, usá *Admin → Productos → Nuevo producto*. Para ingresos de mercadería o conteos, *Stock → Ingreso / Contar*. La planilla vuelve a servir cuando haya que cargar o corregir muchos de una vez.

## 3. El día a día

- **Caja → Abrir caja** al empezar (con el cambio inicial) y **Cerrar caja** al terminar: el sistema te dice **cuánto debería haber** en el cajón y también en cada medio digital (Mercado Pago QR y transferencia, igual que el efectivo). Vos contás el efectivo y mirás en la app de Mercado Pago; si algo no da: en *Caja → revisar pagos* ves los pagos del turno y podés **corregir el ticket** que se cargó con otro medio (queda en el registro), o cargar un **movimiento** (ingreso, gasto, retiro) eligiendo el medio. Si no lo encontrás, registrás la **pérdida** a mano. El cierre es el análisis con lo que hay: si hay diferencia, dejás una nota.
- **Lápiz de precio (✎)**: al lado del precio de cada ítem de la cuenta. Cambia el precio de **ese ítem sólo en esa venta** (ej.: las pelotas a $10.000 por una promo, o las papas a $3.400): pide el motivo (y el código de administración si está activado), el ítem queda marcado con \* en la cuenta y en el ticket, la diferencia suma al total de **Descuentos otorgados** del día y todo queda en el registro de cambios. El precio del producto no cambia.
- **Vender**, **Mesas**, **Barra**: igual que en la demostración. Los pagos parciales, el cobro por separado, el fiado y las devoluciones están en esas pantallas.
- Abajo a la izquierda ves **«Guardado a las 15:42»**. Si alguna vez dice en rojo *NO SE ESTÁ GUARDANDO*, no cierres la ventana: el programa reintenta solo y avisa qué pasa (casi siempre es el disco lleno).
- **Carpeta de hoy** (botón abajo a la izquierda) abre la carpeta del día con el Excel de lo vendido y el registro de cambios, **ya hechos**, sin exportar nada.
- Para cerrar el programa, **Cerrar programa** (abajo a la izquierda). Al cerrar hace una copia de seguridad.

## 4. Todo queda registrado

- *Admin → **Todo lo que cambió*** muestra, hecho solo por el programa, cada cosa que se agrega, se cambia o se borra: ítems que alguien saca de una cuenta, movimientos de caja, ajustes de stock, cambios de precio, turnos agendados o borrados, descuentos, anulaciones… con **quién** y **cuándo**. Lo importante va marcado y se puede filtrar. Con *Detalle* se ve cómo estaba antes y cómo quedó (si algo se borró, queda guardado entero).
- Ese registro **no se puede borrar ni editar**, ni siquiera desde el programa.
- «Quién» es la persona que abrió la caja; si se usó el código de administración, dice «(con código)».

## 5. Dónde está todo (`C:\Predio`)

```
C:\Predio\
  datos\predio.db           La base de datos (un solo archivo). No la abras ni la muevas con el programa abierto.
  dias\2026-10-03\          Una carpeta por día:
      2026-10-03_resumen.xlsx    ventas, detalle, caja, stock, turnos y cambios del día (se actualiza solo)
      2026-10-03_registro.txt    todo lo que cambió ese día, en texto simple
  copias\                   Copias de seguridad de la base (se hacen solas, ver punto 6)
  imagenes\                 Fotos de los productos
  planillas\                Las planillas de productos que bajaste o subiste
  personalizar\             marca.json (el nombre del negocio) y logo.png: se cambian con el Bloc de notas y se ven con F5
  configuracion.json        Ajustes que se pueden cambiar con el Bloc de notas
  LEEME.txt                 Resumen de esta carpeta
```

Los Excel diarios llevan montos, productos y nombres, **sin teléfonos**. Si el Excel del día está abierto en la PC, Windows no deja actualizarlo: se reintenta solo apenas lo cerrás.

**Mirar la base desde otro programa:** instalá *DB Browser for SQLite* (gratis), abrí `predio.db` con *Abrir en modo sólo lectura* y mirá las vistas que empiezan con `v_` (`v_ventas`, `v_venta_items`, `v_productos`, `v_libro_caja`, `v_cambios`…): ya vienen ordenadas en columnas.

**Logo:** un PNG cuadrado de **256 × 256 px** (fondo transparente) llamado `logo.png`; en pantalla se ve a 34 px y también se usa como ícono de la ventana. **Nombre (fijo del negocio):** en `C:\Predio\personalizar\`. Abrí `marca.json` con el Bloc de notas y cambiá el nombre; copiá tu logo en esa carpeta con el nombre `logo.png`. Guardá y apretá **F5** en el programa: no hay que reinstalar nada, y las actualizaciones no tocan esa carpeta.

## 6. Copias de seguridad

- Se hace una copia **al abrir** el programa cada día y otra **al cerrarlo**. Se guardan todas las de los últimos 3 días y una por día hasta cumplir 30 días (se puede cambiar en `configuracion.json`).
- *Admin → Datos y seguridad* tiene **Hacer una copia ahora** y la lista de copias con **Restaurar**. Antes de restaurar el programa guarda otra copia de lo que hay, por si te arrepentís.
- Para estar tranquilo ante un robo o una falla del disco: una vez por semana, **copiá la carpeta `C:\Predio\copias` a un pendrive** (o a Google Drive).
- Si la base se dañara por completo (un corte de luz en mal momento es lo que más la amenaza), al abrir el programa lo detecta, guarda el archivo dañado aparte y vuelve a la última copia sana avisándote.

## 7. Cosas que conviene saber

- **No necesita internet** para nada. Lo único que usaría internet (más adelante) es copiar el calendario de turnos a una planilla en la nube.
- **El código de administración** es una traba para que nadie toque precios o tickets por error; no es una clave de seguridad contra alguien con la PC en la mano.
- **Una sola PC**: la base está en esa PC. Dos ventanas abiertas a la vez no se pisan (la vieja avisa y se recarga), pero no es para usar desde dos computadoras.
- **Años de datos**: el programa carga en pantalla los últimos 35 días de ventas, caja y stock para ser rápido; lo anterior se trae solo cuando mirás tickets o reportes de fechas viejas. Todo sigue guardado.
- **Empezar a usar en serio después de probar**: *Admin → Datos y seguridad → Restablecer → Borrar ventas e historial* (hace una copia antes y pide escribir BORRAR; borra tickets, caja, movimientos de stock, turnos, deudas de clientes y cuentas abiertas, y deja productos con su stock, clientes, categorías, mesas y medios de pago).

## 8. Lo que todavía no está

- **Turnos en la nube** (planilla compartida / celular / bot): el calendario de turnos del programa está apagado por ahora (se prende en *Datos y seguridad*) hasta resolver cómo se conectan con el celular.
- **Mirar ventas desde el celular**: la base ya tiene todo lo necesario (resumen del día, cambios) para mostrarlo en modo sólo lectura; falta elegir cómo (planilla en la nube o acceso por la red local).
- **Lectura de remitos con foto**: en la carga de stock, la lectura de la foto sigue siendo una simulación.
- **Google Sheets** como espejo de los reportes.
