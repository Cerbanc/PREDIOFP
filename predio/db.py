"""Base de datos SQLite: esquema, vistas legibles, migraciones y transacciones.

Diseño:
  - docs:       todos los datos del negocio (un registro por producto, venta, mesa, etc.) en JSON.
                Es la fuente de verdad. Cada cambio se hace dentro de UNA transacción (todo o nada).
  - auditoria:  registro de cambios. Sólo se agrega: unos disparadores (triggers) impiden
                modificarlo o borrarlo, ni siquiera desde el propio programa.
  - v_*:        vistas ya ordenadas en columnas para mirar la base desde otro programa
                (DB Browser for SQLite, Excel con ODBC, Power BI...). Son sólo lectura.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("predio.db")

ESQUEMA_VERSION = 1


class BaseCorrupta(RuntimeError):
    """La verificación de integridad de la base falló."""


SQL_V1 = (
    """CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS colecciones (
    nombre TEXT PRIMARY KEY,
    forma  TEXT NOT NULL CHECK (forma IN ('lista', 'mapa', 'unico'))
)""",
    """CREATE TABLE IF NOT EXISTS docs (
    coleccion TEXT    NOT NULL,
    id        TEXT    NOT NULL,
    seq       INTEGER NOT NULL,   -- orden de alta: así se reconstruyen las listas tal como estaban
    rev       INTEGER NOT NULL,   -- número de revisión global del último cambio
    ts        INTEGER,            -- fecha y hora principal del registro (milisegundos), si tiene
    dia       TEXT,               -- AAAA-MM-DD (hora local) si tiene fecha
    data      TEXT    NOT NULL,   -- el registro en JSON
    PRIMARY KEY (coleccion, id)
) WITHOUT ROWID""",
    """CREATE INDEX IF NOT EXISTS docs_por_dia ON docs (coleccion, dia)""",
    """CREATE INDEX IF NOT EXISTS docs_por_seq ON docs (coleccion, seq)""",
    """CREATE INDEX IF NOT EXISTS docs_por_rev ON docs (rev)""",
    """CREATE TABLE IF NOT EXISTS auditoria (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,                       -- milisegundos
    dia        TEXT    NOT NULL,                       -- AAAA-MM-DD hora local
    usuario    TEXT    NOT NULL,
    coleccion  TEXT    NOT NULL,
    ref        TEXT    NOT NULL,                       -- id del registro afectado
    accion     TEXT    NOT NULL CHECK (accion IN ('alta', 'cambio', 'baja')),
    importante INTEGER NOT NULL DEFAULT 0,             -- 1 = borrados, anulaciones, descuentos, caja, precios...
    resumen    TEXT    NOT NULL,                       -- frase legible
    antes      TEXT,                                   -- JSON: cómo estaba (sólo lo que cambió)
    despues    TEXT,                                   -- JSON: cómo quedó
    rev        INTEGER NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS auditoria_por_dia ON auditoria (dia)""",
    """CREATE INDEX IF NOT EXISTS auditoria_por_ref ON auditoria (coleccion, ref)""",
    """CREATE TRIGGER IF NOT EXISTS auditoria_no_modificar BEFORE UPDATE ON auditoria
BEGIN SELECT RAISE(ABORT, 'El registro de cambios no se puede modificar'); END""",
    """CREATE TRIGGER IF NOT EXISTS auditoria_no_borrar BEFORE DELETE ON auditoria
BEGIN SELECT RAISE(ABORT, 'El registro de cambios no se puede borrar'); END""",
)


# Las vistas se recrean en cada arranque: no guardan datos, sólo ordenan lo que ya está.
VISTAS = {
    "v_productos": """
        SELECT d.id AS id,
               json_extract(d.data, '$.nombre') AS nombre,
               (SELECT json_extract(c.data, '$.nombre') FROM docs c WHERE c.coleccion = 'categorias' AND c.id = json_extract(d.data, '$.catId')) AS categoria,
               json_extract(d.data, '$.tipo') AS tipo,
               json_extract(d.data, '$.precio') AS precio,
               json_extract(d.data, '$.costo') AS costo,
               json_extract(d.data, '$.stock') AS stock,
               json_extract(d.data, '$.minimo') AS minimo,
               json_extract(d.data, '$.paqueteNombre') AS paquete,
               json_extract(d.data, '$.paqueteUnidades') AS unidades_por_paquete,
               json_extract(d.data, '$.activo') AS activo,
               json_extract(d.data, '$.img') AS imagen,
               json_extract(d.data, '$.link') AS link
        FROM docs d WHERE d.coleccion = 'productos'""",
    "v_ventas": """
        SELECT d.id AS id,
               json_extract(d.data, '$.nro') AS nro,
               d.dia AS fecha,
               strftime('%H:%M', d.ts / 1000, 'unixepoch', 'localtime') AS hora,
               json_extract(d.data, '$.destino') AS destino,
               json_extract(d.data, '$.cliente') AS cliente,
               json_extract(d.data, '$.subtotal') AS subtotal,
               json_extract(d.data, '$.descuento') AS descuento,
               COALESCE(json_extract(d.data, '$.descuentoItems'), 0) AS descuento_items,
               json_extract(d.data, '$.total') AS total,
               json_extract(d.data, '$.estado') AS estado,
               json_extract(d.data, '$.ajuste.motivo') AS motivo_descuento,
               (SELECT group_concat(json_extract(p.value, '$.nombre') || ' ' || json_extract(p.value, '$.monto'), ' + ')
                  FROM json_each(d.data, '$.pagos') p) AS medios_de_pago
        FROM docs d WHERE d.coleccion = 'ventas'""",
    "v_venta_items": """
        SELECT d.id AS venta_id,
               json_extract(d.data, '$.nro') AS nro,
               d.dia AS fecha,
               strftime('%H:%M', d.ts / 1000, 'unixepoch', 'localtime') AS hora,
               json_extract(d.data, '$.estado') AS estado,
               json_extract(i.value, '$.nombre') AS producto,
               (SELECT json_extract(c.data, '$.nombre') FROM docs c WHERE c.coleccion = 'categorias' AND c.id = json_extract(i.value, '$.catId')) AS categoria,
               json_extract(i.value, '$.cant') AS cantidad,
               json_extract(i.value, '$.precio') AS precio,
               json_extract(i.value, '$.cant') * json_extract(i.value, '$.precio') AS subtotal,
               json_extract(i.value, '$.precioLista') AS precio_lista,
               json_extract(i.value, '$.motivoPrecio') AS motivo_precio,
               json_extract(i.value, '$.costo') AS costo_unitario,
               json_extract(i.value, '$.nota') AS nota
        FROM docs d, json_each(d.data, '$.items') i WHERE d.coleccion = 'ventas'""",
    "v_pagos_de_ventas": """
        SELECT d.id AS venta_id,
               json_extract(d.data, '$.nro') AS nro,
               d.dia AS fecha,
               json_extract(d.data, '$.estado') AS estado,
               json_extract(p.value, '$.nombre') AS medio,
               json_extract(p.value, '$.monto') AS monto,
               COALESCE(json_extract(p.value, '$.fiado'), 0) AS fiado
        FROM docs d, json_each(d.data, '$.pagos') p WHERE d.coleccion = 'ventas'""",
    "v_libro_caja": """
        SELECT d.id AS id,
               d.dia AS fecha,
               strftime('%H:%M', d.ts / 1000, 'unixepoch', 'localtime') AS hora,
               json_extract(d.data, '$.tipo') AS tipo,
               json_extract(d.data, '$.nombre') AS medio,
               json_extract(d.data, '$.monto') AS monto,
               json_extract(d.data, '$.motivo') AS motivo
        FROM docs d WHERE d.coleccion = 'caja'""",
    "v_cajas": """
        SELECT d.id AS id,
               datetime(json_extract(d.data, '$.abierta') / 1000, 'unixepoch', 'localtime') AS abierta,
               datetime(json_extract(d.data, '$.cerrada') / 1000, 'unixepoch', 'localtime') AS cerrada,
               json_extract(d.data, '$.responsable') AS responsable,
               json_extract(d.data, '$.fondo') AS fondo_inicial,
               json_extract(d.data, '$.esperado') AS esperado,
               json_extract(d.data, '$.contado') AS contado,
               json_extract(d.data, '$.diferencia') AS diferencia,
               json_extract(d.data, '$.retiroFinal') AS retiro_final,
               json_extract(d.data, '$.dejado') AS cambio_dejado,
               json_extract(d.data, '$.nota') AS nota
        FROM docs d WHERE d.coleccion = 'cajas'""",
    "v_movimientos_de_caja": """
        SELECT d.id AS id,
               d.dia AS fecha,
               strftime('%H:%M', d.ts / 1000, 'unixepoch', 'localtime') AS hora,
               json_extract(d.data, '$.tipo') AS tipo,
               (SELECT json_extract(c.data, '$.nombre') FROM docs c WHERE c.coleccion = 'metodos' AND c.id = json_extract(d.data, '$.metodoId')) AS medio,
               json_extract(d.data, '$.concepto') AS concepto,
               json_extract(d.data, '$.proveedor') AS proveedor,
               json_extract(d.data, '$.monto') AS monto,
               json_extract(d.data, '$.nota') AS nota,
               COALESCE(json_extract(d.data, '$.anulado'), 0) AS anulado
        FROM docs d WHERE d.coleccion = 'movCaja'""",
    "v_movimientos_de_stock": """
        SELECT d.id AS id,
               d.dia AS fecha,
               strftime('%H:%M', d.ts / 1000, 'unixepoch', 'localtime') AS hora,
               json_extract(d.data, '$.nombre') AS producto,
               json_extract(d.data, '$.tipo') AS tipo,
               json_extract(d.data, '$.cant') AS cantidad,
               json_extract(d.data, '$.antes') AS stock_antes,
               json_extract(d.data, '$.despues') AS stock_despues,
               json_extract(d.data, '$.motivo') AS motivo
        FROM docs d WHERE d.coleccion = 'movimientos'""",
    "v_clientes": """
        SELECT d.id AS id,
               json_extract(d.data, '$.nombre') AS nombre,
               json_extract(d.data, '$.tel') AS telefono,
               json_extract(d.data, '$.tipo') AS tipo,
               json_extract(d.data, '$.nota') AS nota,
               COALESCE((SELECT SUM(CASE json_extract(e.data, '$.tipo') WHEN 'cargo' THEN json_extract(e.data, '$.monto') ELSE -json_extract(e.data, '$.monto') END)
                           FROM docs e WHERE e.coleccion = 'cc' AND json_extract(e.data, '$.clienteId') = d.id), 0) AS debe
        FROM docs d WHERE d.coleccion = 'clientes'""",
    "v_cuenta_corriente": """
        SELECT d.id AS id,
               d.dia AS fecha,
               strftime('%H:%M', d.ts / 1000, 'unixepoch', 'localtime') AS hora,
               (SELECT json_extract(c.data, '$.nombre') FROM docs c WHERE c.coleccion = 'clientes' AND c.id = json_extract(d.data, '$.clienteId')) AS cliente,
               json_extract(d.data, '$.tipo') AS tipo,
               json_extract(d.data, '$.monto') AS monto,
               json_extract(d.data, '$.ref') AS referencia,
               json_extract(d.data, '$.nota') AS nota
        FROM docs d WHERE d.coleccion = 'cc'""",
    "v_turnos": """
        SELECT d.id AS id,
               json_extract(d.data, '$.fecha') AS fecha,
               (SELECT json_extract(c.data, '$.nombre') FROM docs c WHERE c.coleccion = 'canchas' AND c.id = json_extract(d.data, '$.canchaId')) AS cancha,
               printf('%02d:%02d', json_extract(d.data, '$.ini') / 60 % 24, json_extract(d.data, '$.ini') % 60) AS hora,
               json_extract(d.data, '$.cliente') AS cliente,
               json_extract(d.data, '$.tel') AS telefono,
               json_extract(d.data, '$.precio') AS precio,
               json_extract(d.data, '$.estado') AS estado,
               json_extract(d.data, '$.tipoPrecio') AS tipo_precio,
               COALESCE(json_extract(d.data, '$.esFijo'), 0) AS es_fijo
        FROM docs d WHERE d.coleccion = 'turnos'""",
    "v_cambios": """
        SELECT a.id AS id,
               a.dia AS fecha,
               strftime('%H:%M:%S', a.ts / 1000, 'unixepoch', 'localtime') AS hora,
               a.usuario AS quien,
               a.coleccion AS dato,
               a.accion AS accion,
               a.importante AS importante,
               a.resumen AS detalle,
               a.ref AS id_registro
        FROM auditoria a""",
}


class BaseDeDatos:
    """Una conexión compartida protegida por un candado: el programa escribe de a una operación por vez."""

    def __init__(self, ruta: Path | str, migrar: bool = True):
        self.ruta = Path(ruta)
        self.lock = threading.RLock()
        self.con = self._abrir()
        try:
            self._configurar()
            self.verificar()
        except sqlite3.DatabaseError as e:         # "file is not a database" y similares
            self.con.close()
            raise BaseCorrupta(str(e)) from e
        if migrar:
            self.migrar()

    # ---- conexión ----
    def _abrir(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.ruta), timeout=30, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def lectura(self) -> sqlite3.Connection:
        """Conexión aparte, sólo para leer (la usan los procesos de fondo sin trabar la caja)."""
        con = sqlite3.connect(str(self.ruta), timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        con.execute("PRAGMA busy_timeout = 30000")
        return con

    def _configurar(self) -> None:
        c = self.con
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = FULL")      # cada venta queda escrita en disco antes de confirmarse
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA busy_timeout = 30000")
        c.execute("PRAGMA temp_store = MEMORY")

    def verificar(self, completo: bool = False) -> None:
        """Al abrir sólo se comprueba que el archivo sea una base legible (rápido). La revisión completa (`completo`)
        recorre todas las páginas y con años de datos tarda: se hace en segundo plano."""
        if completo:
            con = self.lectura()
            try:
                fila = con.execute("PRAGMA quick_check").fetchone()
            finally:
                con.close()
        else:
            fila = self.con.execute("PRAGMA schema_version").fetchone()
            self.con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            return
        if not fila or fila[0] != "ok":
            raise BaseCorrupta(str(fila[0] if fila else "sin respuesta"))

    def cerrar(self) -> None:
        with self.lock:
            try:
                self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.con.close()

    # ---- transacciones ----
    @contextmanager
    def transaccion(self):
        """Todo lo que se haga adentro se guarda junto o no se guarda nada (ROLLBACK si hay un error)."""
        with self.lock:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                yield self.con
            except BaseException:
                self._rollback()
                raise
            try:
                self.con.execute("COMMIT")
            except BaseException:
                self._rollback()
                raise

    def _rollback(self) -> None:
        try:
            if self.con.in_transaction:
                self.con.execute("ROLLBACK")
        except sqlite3.Error:
            log.exception("No se pudo deshacer la transacción")

    # ---- meta ----
    def meta(self, clave: str, defecto: str | None = None, con: sqlite3.Connection | None = None) -> str | None:
        fila = (con or self.con).execute("SELECT valor FROM meta WHERE clave = ?", (clave,)).fetchone()
        return fila[0] if fila else defecto

    @staticmethod
    def poner_meta(con: sqlite3.Connection, clave: str, valor) -> None:
        con.execute("INSERT INTO meta (clave, valor) VALUES (?, ?) ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor", (clave, str(valor)))

    # ---- migraciones ----
    def migrar(self) -> None:
        with self.lock:
            actual = self.con.execute("PRAGMA user_version").fetchone()[0]
            if actual > ESQUEMA_VERSION:
                raise RuntimeError(
                    f"La base de datos es de una versión más nueva ({actual}) que este programa ({ESQUEMA_VERSION}). "
                    "Instalá la versión más reciente del programa."
                )
            with self.transaccion() as con:
                if actual < 1:
                    for sentencia in SQL_V1:
                        con.execute(sentencia)
                    self.poner_meta(con, "rev", 0)
                    self.poner_meta(con, "seq", 0)
                    self.poner_meta(con, "creada", _ahora_iso())
                # Próximas versiones: if actual < 2: ... (el programa hace una copia antes de migrar)
                self._crear_vistas(con)
                con.execute(f"PRAGMA user_version = {ESQUEMA_VERSION}")

    def _crear_vistas(self, con: sqlite3.Connection) -> None:
        for nombre, sql in VISTAS.items():
            con.execute(f"DROP VIEW IF EXISTS {nombre}")
            con.execute(f"CREATE VIEW {nombre} AS {sql}")

    def version_actual(self) -> int:
        return self.con.execute("PRAGMA user_version").fetchone()[0]

    def necesita_migrar(self) -> bool:
        return self.version_actual() < ESQUEMA_VERSION


def version_de_archivo(ruta: Path) -> int:
    """Versión de esquema de un archivo de base (para decidir si hace falta copia antes de migrar)."""
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def _ahora_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now().isoformat(timespec="seconds")
