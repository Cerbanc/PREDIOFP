"""Arranque y cierre del programa."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import NOMBRE, VERSION
from .almacen import Almacen
from .config import Rutas, carpeta_por_defecto, carpeta_recursos, leer_ajustes, preparar_log_tecnico
from .copias import Copias
from .db import BaseCorrupta, BaseDeDatos
from .diario import ArchivosDiarios
from .servidor import Aplicacion, Servidor
from .util import dia_de, ahora_ms
from .ventana import abrir_ventana

log = logging.getLogger("predio.app")

LEEME = """CAJA DEL PREDIO - carpeta de datos
===================================

Todo lo que guarda el programa está en esta carpeta. No hay datos en ningún otro lado.

  datos\\predio.db     La base de datos. Es el archivo más importante. No lo abras ni lo muevas con el programa abierto.
  dias\\AAAA-MM-DD\\    Una carpeta por día con el Excel de ese día (se actualiza solo) y el registro de cambios en texto.
  copias\\             Copias de seguridad de la base (una por día, se guardan las últimas semanas).
  imagenes\\           Fotos de los productos.
  planillas\\          Planillas de productos y stock que se bajaron o subieron.
  exportaciones\\      Lo que se exporta desde las pantallas.
  logs_tecnicos\\      Errores internos del programa (sólo para soporte).
  configuracion.json  Ajustes que se pueden cambiar con el Bloc de notas.

Para ver la base desde otro programa: DB Browser for SQLite (gratis) -> abrir datos\\predio.db en modo "sólo lectura".
Las vistas que empiezan con v_ (v_ventas, v_productos, v_cambios...) ya vienen ordenadas en columnas.

Para pasar el programa a otra PC: copiá esta carpeta entera.
"""


def mostrar_error(mensaje: str) -> None:
    """El programa instalado no tiene consola: si algo impide abrirlo, se avisa con una ventana en vez de cerrarse en silencio."""
    log.error(mensaje)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, mensaje, NOMBRE, 0x10)      # type: ignore[attr-defined]
            return
        except Exception:      # noqa: BLE001
            pass
    if sys.stderr is not None:
        print(mensaje, file=sys.stderr)


def asegurar_leeme(rutas: Rutas) -> None:
    destino = rutas.raiz / "LEEME.txt"
    if not destino.exists():
        try:
            destino.write_text(LEEME, encoding="utf-8")
        except OSError:
            pass


def _recuperar_base_corrupta(rutas: Rutas, detalle: str) -> str:
    """La base no pasa la verificación: se guarda aparte y se vuelve a la última copia sana."""
    marca = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    aparte = rutas.datos / f"corrupta_{marca}"
    aparte.mkdir(parents=True, exist_ok=True)
    for sufijo in ("", "-wal", "-shm"):
        origen = Path(str(rutas.base) + sufijo)
        if origen.exists():
            shutil.move(str(origen), str(aparte / origen.name))
    for copia in sorted(rutas.copias.glob("predio_*.db"), reverse=True):
        try:
            import sqlite3

            c = sqlite3.connect(f"file:{copia}?mode=ro", uri=True)
            ok = c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            c.close()
        except Exception:      # noqa: BLE001
            continue
        if ok:
            shutil.copy2(copia, rutas.base)
            return (f"La base de datos estaba dañada ({detalle}). Se recuperó la copia de seguridad «{copia.name}»: "
                    f"se pueden haber perdido los cambios hechos después de esa copia. El archivo dañado quedó en {aparte}.")
    return (f"La base de datos estaba dañada ({detalle}) y no había ninguna copia sana. Se empieza de cero. "
            f"El archivo dañado quedó guardado en {aparte} por si se puede rescatar.")


def crear_aplicacion(rutas: Rutas, ajustes: dict, modo: str = "real") -> Aplicacion:
    avisos: list[str] = []
    try:
        db = BaseDeDatos(rutas.base, migrar=False)
    except BaseCorrupta as e:
        aviso = _recuperar_base_corrupta(rutas, str(e))
        log.error(aviso)
        avisos.append(aviso)
        db = BaseDeDatos(rutas.base, migrar=False)
    almacen = Almacen(db, rutas)
    copias = Copias(db, almacen, rutas, ajustes["copias_conservar_dias"])
    if db.version_actual() > 0 and db.necesita_migrar():
        copias.crear("seguridad_antes_de_migrar")
    db.migrar()
    diario = ArchivosDiarios(db, rutas)
    app = Aplicacion(rutas=rutas, ajustes=ajustes, db=db, almacen=almacen, copias=copias, diario=diario, modo=modo, avisos=avisos)
    preparar_pagina(app)
    return app


def preparar_pagina(app: Aplicacion) -> None:
    config = json.dumps({"token": app.token, "version": VERSION, "modo": app.modo, "negocio": NOMBRE}, ensure_ascii=False)
    marcador = "<!--PREDIO-CONFIG-->"
    base = (carpeta_recursos() / "web" / "index.html").read_text(encoding="utf-8")
    if marcador not in base:
        raise RuntimeError("La pantalla no tiene el marcador de configuración")
    app.html = base.replace(marcador, f"<script>window.PREDIO={config};</script>")


def tareas_de_inicio(app: Aplicacion, espera: float = 20.0) -> None:
    """Cosas que no hace falta esperar para empezar a vender: revisión de la base, copia del día, limpieza y archivos de hoy.
    Arrancan unos segundos después de abrir para no competir con la primera carga de la pantalla."""
    if app.apagar.wait(espera):
        return
    try:
        app.db.verificar(completo=True)
    except BaseCorrupta as e:
        log.error("La revisión de la base falló: %s", e)
        app.avisos.append("La revisión automática encontró un problema en la base de datos. Se hace una copia ahora; avisá a soporte antes de seguir trabajando.")
    except Exception:      # noqa: BLE001
        log.exception("No se pudo revisar la base")
    try:
        app.copias.asegurar_copia_del_dia()
        app.copias.limpiar()
    except Exception:      # noqa: BLE001
        log.exception("Falló la copia de seguridad de inicio")
        app.avisos.append("No se pudo hacer la copia de seguridad automática de hoy. Revisá el espacio libre del disco.")
    hoy = dia_de(ahora_ms())
    ayer = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    app.diario.marcar([hoy] + ([ayer] if (app.rutas.dias / ayer).exists() else []))


def cerrar_aplicacion(app: Aplicacion) -> None:
    try:
        app.diario.detener(escribir_pendientes=True)
    except Exception:      # noqa: BLE001
        log.exception("Error al cerrar los archivos diarios")
    try:
        if app.copias.hay_cambios_sin_copiar():
            app.copias.crear("cierre")
        app.copias.limpiar()
    except Exception:      # noqa: BLE001
        log.exception("Error al hacer la copia de cierre")
    app.db.cerrar()


def hay_otra_instancia(puerto: int, rutas: Rutas) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/ping", timeout=1.5) as r:
            datos = json.loads(r.read().decode())
        return datos.get("app") == "predio" and Path(datos.get("datos", "")).resolve() == rutas.raiz.resolve()
    except Exception:      # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="predio", description=NOMBRE)
    ap.add_argument("--datos", help="Carpeta donde se guardan los datos (por defecto C:\\Predio)")
    ap.add_argument("--demo", action="store_true", help="Usa una carpeta aparte con datos de ejemplo, para probar o mostrar")
    ap.add_argument("--sin-ventana", action="store_true", help="No abre la ventana (sólo el servidor)")
    ap.add_argument("--puerto", type=int, help="Puerto local (por defecto el de configuracion.json)")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        print(f"{NOMBRE} {VERSION}")
        return 0

    raiz = Path(args.datos).expanduser() if args.datos else carpeta_por_defecto()
    if args.demo:
        raiz = raiz.with_name(raiz.name + "_demo")
    rutas = Rutas(raiz).crear()
    preparar_log_tecnico(rutas)
    ajustes = leer_ajustes(rutas)
    puerto = args.puerto or ajustes["puerto"] + (1 if args.demo else 0)
    modo = "demo" if args.demo else "real"

    if hay_otra_instancia(puerto, rutas):
        log.info("El programa ya está abierto: se muestra esa ventana")
        if not args.sin_ventana:
            abrir_ventana(f"http://127.0.0.1:{puerto}/", rutas.perfil_navegador, ajustes["ventana_completa"])
        return 0

    asegurar_leeme(rutas)
    try:
        app = crear_aplicacion(rutas, ajustes, modo)
        servidor = Servidor(app, puerto)
    except Exception as e:      # noqa: BLE001
        log.exception("No se pudo iniciar el programa")
        mostrar_error(f"No se pudo iniciar el programa.\n\n{e}\n\nLos detalles quedaron en {rutas.tecnicos / 'predio.log'}")
        return 1
    app.diario.iniciar()
    servidor.iniciar()
    espera = float(os.environ.get("PREDIO_ESPERA_INICIO", "20"))
    threading.Thread(target=tareas_de_inicio, args=(app, espera), name="inicio", daemon=True).start()
    log.info("%s %s en %s (datos: %s, modo %s)", NOMBRE, VERSION, servidor.url, rutas.raiz, modo)

    def salir(*_):
        app.apagar.set()

    for nombre in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, nombre):
            try:
                signal.signal(getattr(signal, nombre), salir)
            except (ValueError, OSError):
                pass

    proceso = None if args.sin_ventana else abrir_ventana(servidor.url, rutas.perfil_navegador, ajustes["ventana_completa"])
    t0 = time.time()
    while not app.apagar.wait(0.5):
        if proceso is not None and proceso.poll() is not None and time.time() - t0 > 6:
            time.sleep(1.0)
            break
    log.info("Cerrando...")
    servidor.parar()
    cerrar_aplicacion(app)
    if proceso is not None and proceso.poll() is None:      # si se cerró desde el botón del programa, se cierra también la ventana
        proceso.terminate()
        try:
            proceso.wait(timeout=5)
        except Exception:      # noqa: BLE001
            pass
    return 0
