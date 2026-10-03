"""Copias de seguridad de la base de datos.

Se hacen con la API de copia de SQLite (siempre queda un archivo consistente, aunque se esté vendiendo)
y se verifican antes de guardarlas. Nunca se pisa una copia: cada una lleva fecha y hora.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path

from .almacen import Almacen
from .config import Rutas
from .db import ESQUEMA_VERSION, BaseDeDatos
from .util import nombre_seguro

log = logging.getLogger("predio.copias")
PATRON = re.compile(r"^predio_(\d{4}-\d{2}-\d{2})_(\d{4})_([A-Za-z0-9_-]+)\.db$")


class CopiaInvalida(Exception):
    pass


class Copias:
    def __init__(self, db: BaseDeDatos, almacen: Almacen, rutas: Rutas, conservar_dias: int = 30):
        self.db, self.almacen, self.rutas, self.conservar_dias = db, almacen, rutas, conservar_dias
        self._lock = threading.Lock()

    # ---- crear ----
    def crear(self, motivo: str = "manual") -> Path:
        motivo = nombre_seguro(motivo)[:30]
        with self._lock:
            ahora = dt.datetime.now()
            nombre = f"predio_{ahora:%Y-%m-%d_%H%M}_{motivo}.db"
            destino = self.rutas.copias / nombre
            n = 1
            while destino.exists():
                n += 1
                destino = self.rutas.copias / f"predio_{ahora:%Y-%m-%d_%H%M}_{motivo}{n}.db"
            tmp = destino.with_name("." + destino.name + ".tmp")
            origen = self.db.lectura()
            copia = sqlite3.connect(str(tmp))
            try:
                origen.backup(copia)
                chequeo = copia.execute("PRAGMA quick_check").fetchone()
                if not chequeo or chequeo[0] != "ok":
                    raise CopiaInvalida(f"La copia no pasó la verificación: {chequeo}")
            except BaseException:
                copia.close()
                origen.close()
                tmp.unlink(missing_ok=True)
                raise
            copia.close()
            origen.close()
            os.replace(tmp, destino)
            with self.db.lock:
                self.db.poner_meta(self.db.con, "rev_ultima_copia", self.almacen.rev())
            log.info("Copia de seguridad creada: %s", destino.name)
            return destino

    def hay_cambios_sin_copiar(self) -> bool:
        return self.almacen.rev() != int(self.db.meta("rev_ultima_copia", "-1") or -1)

    def asegurar_copia_del_dia(self) -> Path | None:
        """Al abrir el programa: si todavía no hay una copia de hoy, se hace."""
        hoy = dt.date.today().isoformat()
        if any(c["dia"] == hoy for c in self.listar()):
            return None
        return self.crear("inicio")

    # ---- listar y limpiar ----
    def listar(self) -> list[dict]:
        out = []
        for p in sorted(self.rutas.copias.glob("predio_*.db"), reverse=True):
            m = PATRON.match(p.name)
            if not m:
                continue
            st = p.stat()
            out.append({"archivo": p.name, "dia": m.group(1), "hora": m.group(2)[:2] + ":" + m.group(2)[2:], "motivo": m.group(3),
                        "bytes": st.st_size, "modificada": int(st.st_mtime * 1000)})
        return out

    def limpiar(self) -> int:
        """Se conservan todas las de los últimos 3 días y una por día hasta cumplir `conservar_dias`."""
        hoy = dt.date.today()
        borradas = 0
        por_dia: dict[str, list[dict]] = {}
        for c in self.listar():
            por_dia.setdefault(c["dia"], []).append(c)
        for dia, copias in por_dia.items():
            edad = (hoy - dt.date.fromisoformat(dia)).days
            quitar: list[dict] = []
            if edad > self.conservar_dias:
                quitar = [c for c in copias if not c["motivo"].startswith("seguridad")]
            elif edad > 3:
                normales = [c for c in copias if not c["motivo"].startswith("seguridad")]
                quitar = sorted(normales, key=lambda c: c["hora"])[:-1]
            if edad > 90:
                quitar = copias
            for c in quitar:
                try:
                    (self.rutas.copias / c["archivo"]).unlink()
                    borradas += 1
                except OSError:
                    log.warning("No se pudo borrar la copia vieja %s", c["archivo"])
        return borradas

    # ---- restaurar ----
    def _ruta_segura(self, nombre: str) -> Path:
        if not PATRON.match(nombre or ""):
            raise CopiaInvalida("Nombre de copia inválido")
        ruta = self.rutas.copias / nombre
        if not ruta.is_file():
            raise CopiaInvalida("Esa copia no existe")
        return ruta

    def restaurar(self, nombre: str, usuario: str = "Caja") -> dict:
        ruta = self._ruta_segura(nombre)
        src = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
        try:
            chequeo = src.execute("PRAGMA quick_check").fetchone()
            if not chequeo or chequeo[0] != "ok":
                raise CopiaInvalida("La copia está dañada y no se puede restaurar")
            if src.execute("PRAGMA user_version").fetchone()[0] > ESQUEMA_VERSION:
                raise CopiaInvalida("La copia es de una versión más nueva del programa")
            self.crear("seguridad_antes_de_restaurar")
            with self.db.lock:
                rev_antes = self.almacen.rev()
                src.backup(self.db.con)
                self.db._configurar()
                self.db.migrar()
                with self.db.transaccion() as con:
                    rev_copia = int(self.db.meta("rev", "0", con) or 0)
                    self.db.poner_meta(con, "rev", max(rev_antes, rev_copia) + 1)
            self.almacen.registrar_evento(usuario, f"Se restauró la copia de seguridad {nombre}", True, "sistema", nombre)
        finally:
            src.close()
        return {"rev": self.almacen.rev()}
