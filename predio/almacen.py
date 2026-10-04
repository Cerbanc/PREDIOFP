"""Guardado de datos: cada cambio entra en una transacción y deja su línea en el registro de cambios.

La pantalla trabaja con todos los datos en memoria y manda sólo lo que cambió. Acá se aplica
todo junto (o nada) y se le devuelve la revisión nueva. Si dos ventanas intentan guardar a la vez,
la que tiene una versión vieja es rechazada (ConflictoDeRevision) y se recarga: nunca se pisan datos.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
from typing import Any

from . import auditoria
from .config import Rutas
from .db import BaseDeDatos
from .util import ahora_ms, dia_de, es_dia

log = logging.getLogger("predio.almacen")

FORMAS_VALIDAS = ("lista", "mapa", "unico")
NOMBRE_COLECCION = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
MAX_DOC_BYTES = 3_000_000
MAX_IMAGEN_BYTES = 8_000_000
DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$", re.S)
CAMPO_FECHA = {"cajas": "abierta", "clientes": "creado"}
# Lo que crece todos los días: la pantalla sólo carga los últimos días y pide el resto cuando hace falta.
HISTORICAS = ("ventas", "caja", "movimientos", "movCaja", "log")
# Lo que "Borrar ventas e historial" deja en cero (los productos, clientes, medios de pago y mesas se conservan).
HISTORIAL_A_BORRAR = ("ventas", "movimientos", "caja", "movCaja", "cajas", "turnos", "cc")


class ConflictoDeRevision(Exception):
    """La pantalla tenía una versión vieja de los datos."""

    def __init__(self, rev_actual: int):
        super().__init__("Los datos cambiaron desde otra ventana")
        self.rev_actual = rev_actual


class DatosInvalidos(ValueError):
    """El pedido de guardado no es válido."""


def _tiempo_doc(coleccion: str, doc: Any) -> tuple[int | None, str | None]:
    if not isinstance(doc, dict):
        return None, None
    if coleccion == "turnos" and es_dia(doc.get("fecha")):
        return None, doc["fecha"]
    v = doc.get(CAMPO_FECHA.get(coleccion, "ts"))
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 10**11:
        try:
            return int(v), dia_de(v)
        except (OverflowError, OSError, ValueError):
            return None, None
    return None, None


class Almacen:
    def __init__(self, db: BaseDeDatos, rutas: Rutas):
        self.db = db
        self.rutas = rutas

    # ---------------------------------------------------------------- lectura
    def rev(self, con: sqlite3.Connection | None = None) -> int:
        return int(self.db.meta("rev", "0", con) or 0)

    def formas(self, con: sqlite3.Connection | None = None) -> dict[str, str]:
        return {r[0]: r[1] for r in (con or self.db.con).execute("SELECT nombre, forma FROM colecciones")}

    def estado_json(self, ventana_dias: int = 0) -> str:
        """Todos los datos como texto JSON (se arma sin decodificar cada registro: es rápido).

        Con ventana_dias > 0, las colecciones que crecen todos los días (ventas, caja, stock...) traen sólo los últimos
        días; lo anterior se pide con historia(). `ventana` dice desde qué día está cargado (o null si está todo)."""
        desde = (dt.date.today() - dt.timedelta(days=ventana_dias)).isoformat() if ventana_dias > 0 else None
        with self.db.lock:
            con = self.db.con
            rev = self.rev(con)
            formas = self.formas(con)
            if not formas:
                return json.dumps({"rev": rev, "vacia": True, "db": None, "ventana": None})
            partes: dict[str, list[str]] = {n: [] for n, f in formas.items() if f in ("lista", "mapa")}
            unicos: dict[str, str] = {}
            if desde:
                filas: Any = con.execute(
                    "SELECT coleccion, id, data FROM docs WHERE coleccion NOT IN (%s) OR dia IS NULL OR dia >= ? ORDER BY coleccion, seq" % ",".join("?" * len(HISTORICAS)),
                    (*HISTORICAS, desde))
            else:
                filas = con.execute("SELECT coleccion, id, data FROM docs ORDER BY coleccion, seq")
            for coleccion, id_, data in filas:
                f = formas.get(coleccion)
                if f == "lista":
                    partes[coleccion].append(data)
                elif f == "mapa":
                    partes[coleccion].append(json.dumps(id_, ensure_ascii=False) + ":" + data)
                elif f == "unico":
                    unicos[coleccion] = data
            trozos = []
            for nombre, forma in formas.items():
                k = json.dumps(nombre)
                if forma == "lista":
                    trozos.append(f"{k}:[" + ",".join(partes[nombre]) + "]")
                elif forma == "mapa":
                    trozos.append(f"{k}:{{" + ",".join(partes[nombre]) + "}")
                else:
                    trozos.append(f"{k}:" + unicos.get(nombre, "null"))
            return '{"rev":%d,"vacia":false,"ventana":%s,"db":{%s}}' % (rev, json.dumps(desde), ",".join(trozos))

    def historia(self, desde: str, hasta: str) -> dict[str, list[dict]]:
        """Registros viejos (ventas, caja, stock...) de un rango de días, para ampliar lo que la pantalla tiene cargado."""
        if not (es_dia(desde) and es_dia(hasta)):
            raise DatosInvalidos("Fechas inválidas")
        out: dict[str, list[dict]] = {}
        with self.db.lock:
            for c in HISTORICAS:
                filas = self.db.con.execute("SELECT data FROM docs WHERE coleccion = ? AND dia >= ? AND dia <= ? ORDER BY seq", (c, desde, hasta)).fetchall()
                out[c] = [json.loads(r[0]) for r in filas]
        return out

    def borrar_historial(self, usuario: str = "Caja") -> dict:
        """Deja en cero ventas, caja, stock movido, turnos y deudas (productos, clientes, mesas y medios de pago se conservan).
        Es una sola transacción: o se borra todo o no se borra nada. El motivo y las cantidades quedan en el registro de cambios."""
        usuario = (str(usuario or "Caja").strip() or "Caja")[:80]
        ts = ahora_ms()
        with self.db.transaccion() as con:
            rev1 = self.rev(con) + 1
            cuentas = {}
            for c in HISTORIAL_A_BORRAR:
                cuentas[c] = con.execute("SELECT COUNT(*) FROM docs WHERE coleccion = ?", (c,)).fetchone()[0]
                con.execute("DELETE FROM docs WHERE coleccion = ?", (c,))
            for c in HISTORIAL_A_BORRAR:                       # la pantalla espera ver las colecciones, aunque vacías
                if c not in self.formas(con):
                    con.execute("INSERT INTO colecciones (nombre, forma) VALUES (?, 'lista')", (c,))
            fila = con.execute("SELECT data FROM docs WHERE coleccion = 'contadores' AND id = '_'").fetchone()
            cont = json.loads(fila[0]) if fila else {}
            cont["venta"] = 0
            seq = int(self.db.meta("seq", "0", con) or 0)
            if fila:
                con.execute("UPDATE docs SET data = ?, rev = ? WHERE coleccion = 'contadores' AND id = '_'", (json.dumps(cont), rev1))
            con.execute("DELETE FROM docs WHERE coleccion = 'comandas'")
            seq += 1
            con.execute("INSERT INTO docs (coleccion, id, seq, rev, ts, dia, data) VALUES ('comandas', 'M', ?, ?, NULL, NULL, ?)", (seq, rev1, '{"items":[]}'))
            self.db.poner_meta(con, "seq", seq)
            self.db.poner_meta(con, "rev", rev1)
            detalle = ", ".join(f"{n} {c}" for c, n in cuentas.items() if n)
            con.execute(
                "INSERT INTO auditoria (ts, dia, usuario, coleccion, ref, accion, importante, resumen, rev) VALUES (?, ?, ?, 'sistema', '', 'baja', 1, ?, ?)",
                (ts, dia_de(ts), usuario, f"SE BORRÓ EL HISTORIAL (había una copia de seguridad hecha antes): {detalle or 'no había nada para borrar'}", rev1))
        return {"rev": rev1, "borrados": cuentas}

    def esta_vacia(self) -> bool:
        return not self.formas()

    def contar(self, coleccion: str) -> int:
        return self.db.con.execute("SELECT COUNT(*) FROM docs WHERE coleccion = ?", (coleccion,)).fetchone()[0]

    # ---------------------------------------------------------------- imágenes
    def guardar_imagen(self, data_url: str) -> str:
        m = DATA_URL.match(data_url or "")
        if not m:
            raise DatosInvalidos("La imagen no tiene un formato válido")
        try:
            crudo = base64.b64decode(m.group(2), validate=False)
        except ValueError as e:
            raise DatosInvalidos("La imagen está dañada") from e
        if len(crudo) > MAX_IMAGEN_BYTES:
            raise DatosInvalidos("La imagen es demasiado grande")
        return "/img/" + self.guardar_bytes_imagen(crudo, m.group(1))

    def guardar_bytes_imagen(self, crudo: bytes, tipo: str) -> str:
        ext = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp", "gif": "gif"}[tipo]
        nombre = f"{hashlib.sha256(crudo).hexdigest()[:20]}.{ext}"
        destino = self.rutas.imagenes / nombre
        if not destino.exists():
            tmp = destino.with_suffix(destino.suffix + ".tmp")
            tmp.write_bytes(crudo)
            os.replace(tmp, destino)
        return nombre

    def _preparar_doc(self, coleccion: str, id_: str, doc: Any, reemplazos: list[dict]) -> Any:
        if coleccion == "productos" and isinstance(doc, dict):
            img = doc.get("img")
            if isinstance(img, str) and img.startswith("data:image/"):
                url = self.guardar_imagen(img)
                doc = {**doc, "img": url}
                reemplazos.append({"c": coleccion, "id": id_, "campo": "img", "valor": url})
        return doc

    # ---------------------------------------------------------------- escritura
    def aplicar(self, payload: dict, usuario: str = "Caja", base_rev: int | None = None) -> dict:
        ops = payload.get("ops") or []
        formas_nuevas = payload.get("formas") or {}
        if not isinstance(ops, list) or not isinstance(formas_nuevas, dict):
            raise DatosInvalidos("Pedido de guardado mal armado")
        usuario = (str(usuario or "Caja").strip() or "Caja")[:80]
        ts = ahora_ms()
        reemplazos: list[dict] = []
        dias: set[str] = {dia_de(ts)}
        with self.db.transaccion() as con:
            rev0 = self.rev(con)
            if base_rev is not None and int(base_rev) != rev0:
                raise ConflictoDeRevision(rev0)
            formas = self.formas(con)
            hubo_formas = False
            for nombre, forma in formas_nuevas.items():
                if not isinstance(nombre, str) or not NOMBRE_COLECCION.match(nombre) or forma not in FORMAS_VALIDAS:
                    raise DatosInvalidos(f"Colección inválida: {nombre!r}")
                if nombre in formas:
                    if formas[nombre] != forma:
                        raise DatosInvalidos(f"La colección {nombre} ya existe con otra forma")
                    continue
                con.execute("INSERT INTO colecciones (nombre, forma) VALUES (?, ?)", (nombre, forma))
                formas[nombre] = forma
                hubo_formas = True

            rev1 = rev0 + 1
            seq = int(self.db.meta("seq", "0", con) or 0)
            cambios: list[tuple[str, str, Any, Any]] = []
            for op in ops:
                if not isinstance(op, dict) or "c" not in op or "id" not in op:
                    raise DatosInvalidos("Cambio mal armado")
                col, id_ = op["c"], str(op["id"])
                forma = formas.get(col)
                if forma is None:
                    raise DatosInvalidos(f"Colección desconocida: {col!r}")
                if len(id_) > 200:
                    raise DatosInvalidos("Identificador demasiado largo")
                fila = con.execute("SELECT data FROM docs WHERE coleccion = ? AND id = ?", (col, id_)).fetchone()
                antes = json.loads(fila[0]) if fila else None
                if op.get("del"):
                    if fila is None:
                        continue
                    con.execute("DELETE FROM docs WHERE coleccion = ? AND id = ?", (col, id_))
                    cambios.append((col, id_, antes, None))
                    ts_d, dia_d = _tiempo_doc(col, antes)
                    if dia_d:
                        dias.add(dia_d)
                    continue
                if "d" not in op:
                    raise DatosInvalidos("Falta el contenido del cambio")
                nuevo = self._preparar_doc(col, id_, op["d"], reemplazos)
                if forma != "unico" and not isinstance(nuevo, dict):
                    raise DatosInvalidos(f"Los registros de {col} tienen que ser objetos")
                if fila is not None and antes == nuevo:
                    continue
                texto = json.dumps(nuevo, ensure_ascii=False, separators=(",", ":"))
                if len(texto) > MAX_DOC_BYTES:
                    raise DatosInvalidos(f"Un registro de {col} es demasiado grande")
                ts_d, dia_d = _tiempo_doc(col, nuevo)
                if fila is None:
                    seq += 1
                    con.execute("INSERT INTO docs (coleccion, id, seq, rev, ts, dia, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (col, id_, seq, rev1, ts_d, dia_d, texto))
                else:
                    con.execute("UPDATE docs SET rev = ?, ts = ?, dia = ?, data = ? WHERE coleccion = ? AND id = ?",
                                (rev1, ts_d, dia_d, texto, col, id_))
                cambios.append((col, id_, antes, nuevo))
                if dia_d:
                    dias.add(dia_d)
                _, dia_a = _tiempo_doc(col, antes)
                if dia_a:
                    dias.add(dia_a)

            if not cambios and not hubo_formas:
                return {"rev": rev0, "cambios": 0, "reemplazos": [], "dias": []}

            self.db.poner_meta(con, "rev", rev1)
            self.db.poner_meta(con, "seq", seq)
            try:
                entradas = auditoria.generar(con, cambios, formas)
            except Exception:      # noqa: BLE001 - un resumen que falla no puede frenar una venta
                log.exception("Falló el resumen del registro de cambios; se anota una línea genérica")
                entradas = [auditoria.Entrada(c, i, "baja" if d is None else ("alta" if a is None else "cambio"), True,
                                              f"Cambio en {c} ({i}) que el programa no pudo resumir", a if isinstance(a, dict) else None, d if isinstance(d, dict) else None)
                            for c, i, a, d in cambios if c not in auditoria.IGNORAR_COLECCIONES]
            dia_hoy = dia_de(ts)
            for e in entradas:
                antes_j, despues_j = auditoria.serializar(e)
                if e.accion == "alta" and e.coleccion not in auditoria.ALTA_CON_DETALLE:
                    despues_j = None          # el registro nuevo ya está guardado entero; repetirlo sólo haría crecer la base
                con.execute(
                    "INSERT INTO auditoria (ts, dia, usuario, coleccion, ref, accion, importante, resumen, antes, despues, rev) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, dia_hoy, usuario, e.coleccion, e.ref, e.accion, 1 if e.importante else 0, e.resumen, antes_j, despues_j, rev1),
                )
        return {"rev": rev1, "cambios": len(cambios), "reemplazos": reemplazos, "dias": sorted(dias), "avisos": len(entradas)}

    # ---------------------------------------------------------------- consultas para pantallas y paneles
    def auditoria(self, desde: str | None = None, hasta: str | None = None, texto: str = "", importantes: bool = False,
                  limite: int = 300, desplazamiento: int = 0) -> list[dict]:
        sql = ["SELECT id, ts, dia, usuario, coleccion, ref, accion, importante, resumen FROM auditoria WHERE 1 = 1"]
        args: list[Any] = []
        if desde and es_dia(desde):
            sql.append("AND dia >= ?")
            args.append(desde)
        if hasta and es_dia(hasta):
            sql.append("AND dia <= ?")
            args.append(hasta)
        if importantes:
            sql.append("AND importante = 1")
        if texto:
            sql.append("AND (resumen LIKE ? OR usuario LIKE ?)")
            args += [f"%{texto}%", f"%{texto}%"]
        sql.append("ORDER BY id DESC LIMIT ? OFFSET ?")
        args += [max(1, min(int(limite), 2000)), max(0, int(desplazamiento))]
        with self.db.lock:
            return [dict(r) for r in self.db.con.execute(" ".join(sql), args)]

    def detalle_auditoria(self, id_: int) -> dict | None:
        with self.db.lock:
            fila = self.db.con.execute("SELECT * FROM auditoria WHERE id = ?", (id_,)).fetchone()
        return dict(fila) if fila else None

    def registrar_evento(self, usuario: str, resumen: str, importante: bool = True, coleccion: str = "sistema", ref: str = "") -> None:
        """Deja una línea en el registro por algo que no pasa por la pantalla (restaurar copia, borrar historial...)."""
        ts = ahora_ms()
        with self.db.transaccion() as con:
            con.execute(
                "INSERT INTO auditoria (ts, dia, usuario, coleccion, ref, accion, importante, resumen, rev) VALUES (?, ?, ?, ?, ?, 'cambio', ?, ?, ?)",
                (ts, dia_de(ts), usuario, coleccion, ref, 1 if importante else 0, resumen, self.rev(con)),
            )
