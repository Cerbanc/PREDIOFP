"""Carpetas y ajustes.

Todo lo que el programa guarda vive en UNA carpeta (por defecto C:\\Predio en Windows).
El programa se instala en otro lado, así que actualizarlo nunca toca los datos.
No se usa Documentos porque suele estar sincronizado con OneDrive, y una base de datos
abierta no tiene que sincronizarse mientras se usa.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass
from pathlib import Path

AJUSTES_POR_DEFECTO = {
    "_ayuda": (
        "Este archivo se puede editar con el Bloc de notas. Cerrá y volvé a abrir el programa para que tome los cambios. "
        "puerto: puerto local del programa. copias_conservar_dias: cuántos días de copias de seguridad se guardan. "
        "ventana_completa: abrir la ventana ocupando toda la pantalla. "
        "ventana_dias: cuántos días de ventas, caja y stock se cargan en pantalla al abrir (lo anterior se pide al mirar reportes o tickets viejos; "
        "siempre está guardado). 0 = cargar todo."
    ),
    "puerto": 8765,
    "copias_conservar_dias": 30,
    "ventana_completa": True,
    "ventana_dias": 35,
}


@dataclass(frozen=True)
class Rutas:
    raiz: Path

    @property
    def datos(self) -> Path:
        return self.raiz / "datos"

    @property
    def base(self) -> Path:
        return self.datos / "predio.db"

    @property
    def dias(self) -> Path:
        return self.raiz / "dias"

    @property
    def copias(self) -> Path:
        return self.raiz / "copias"

    @property
    def imagenes(self) -> Path:
        return self.raiz / "imagenes"

    @property
    def planillas(self) -> Path:
        return self.raiz / "planillas"

    @property
    def exportaciones(self) -> Path:
        return self.raiz / "exportaciones"

    @property
    def tecnicos(self) -> Path:
        return self.raiz / "logs_tecnicos"

    @property
    def personalizar(self) -> Path:
        return self.raiz / "personalizar"

    @property
    def perfil_navegador(self) -> Path:
        return self.datos / "perfil_ventana"

    @property
    def ajustes(self) -> Path:
        return self.raiz / "configuracion.json"

    def crear(self) -> "Rutas":
        for p in (self.raiz, self.personalizar, self.datos, self.dias, self.copias, self.imagenes, self.planillas, self.exportaciones, self.tecnicos):
            p.mkdir(parents=True, exist_ok=True)
        return self


def carpeta_por_defecto() -> Path:
    """Elige dónde viven los datos: variable PREDIO_DATOS, o C:\\Predio, o ~/Predio."""
    env = os.environ.get("PREDIO_DATOS")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        raiz = Path(os.environ.get("SystemDrive", "C:") + "\\") / "Predio"
        try:
            raiz.mkdir(parents=True, exist_ok=True)
            probe = raiz / ".permiso"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return raiz
        except OSError:
            pass
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Predio"
    return Path.home() / "Predio"


def leer_ajustes(rutas: Rutas) -> dict:
    """Lee configuracion.json; si no existe lo crea con los valores por defecto."""
    ajustes = dict(AJUSTES_POR_DEFECTO)
    if rutas.ajustes.exists():
        try:
            leidos = json.loads(rutas.ajustes.read_text(encoding="utf-8"))
            if isinstance(leidos, dict):
                ajustes.update({k: v for k, v in leidos.items() if k in AJUSTES_POR_DEFECTO})
        except (OSError, ValueError):
            logging.getLogger("predio").warning("configuracion.json ilegible, se usan los valores por defecto")
    else:
        try:
            rutas.ajustes.write_text(json.dumps(AJUSTES_POR_DEFECTO, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    try:
        ajustes["puerto"] = int(ajustes["puerto"])
        ajustes["copias_conservar_dias"] = max(1, int(ajustes["copias_conservar_dias"]))
        ajustes["ventana_dias"] = max(0, int(ajustes["ventana_dias"]))
    except (TypeError, ValueError):
        ajustes["puerto"] = AJUSTES_POR_DEFECTO["puerto"]
        ajustes["copias_conservar_dias"] = AJUSTES_POR_DEFECTO["copias_conservar_dias"]
        ajustes["ventana_dias"] = AJUSTES_POR_DEFECTO["ventana_dias"]
    return ajustes


def preparar_log_tecnico(rutas: Rutas) -> logging.Logger:
    """Log técnico del programa (errores internos). No es el registro de cambios del negocio."""
    log = logging.getLogger("predio")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        h = logging.handlers.RotatingFileHandler(rutas.tecnicos / "predio.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(fmt)
        log.addHandler(h)
    except OSError:
        pass
    if sys.stderr is not None:
        s = logging.StreamHandler(sys.stderr)
        s.setFormatter(fmt)
        log.addHandler(s)
    return log


def carpeta_recursos() -> Path:
    """Carpeta con los archivos del programa (la interfaz). Funciona instalado (PyInstaller) y desde el código."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "predio"
    return Path(__file__).resolve().parent


MARCA_POR_DEFECTO = {"nombre": "Predio Fútbol-Pádel", "logo": "logo.png"}
LEEME_PERSONALIZAR = """PERSONALIZAR EL PROGRAMA
========================

marca.json  -> el nombre que aparece arriba a la izquierda y en los tickets, y el nombre del archivo del logo.
logo.png    -> el logo (PNG, JPG o WEBP; cuadrado o apaisado, idealmente de 200 px de alto o más).

Para ver un cambio: guardá el archivo y en el programa apretá F5 (no hace falta cerrar nada ni reinstalar).
Este archivo y el logo NO se tocan al actualizar el programa.
Si borrás marca.json, se vuelve a crear con los valores de fábrica.
"""


def leer_marca(rutas: Rutas) -> dict:
    """Nombre y logo del negocio. Se leen de C:\\Predio\\personalizar cada vez que se abre la pantalla."""
    marca = dict(MARCA_POR_DEFECTO)
    ruta = rutas.personalizar / "marca.json"
    try:
        rutas.personalizar.mkdir(parents=True, exist_ok=True)
        if ruta.exists():
            leido = json.loads(ruta.read_text(encoding="utf-8-sig"))
            if isinstance(leido, dict):
                if isinstance(leido.get("nombre"), str) and leido["nombre"].strip():
                    marca["nombre"] = leido["nombre"].strip()[:60]
                if isinstance(leido.get("logo"), str):
                    marca["logo"] = leido["logo"].strip()
        else:
            ruta.write_text(json.dumps(MARCA_POR_DEFECTO, indent=2, ensure_ascii=False), encoding="utf-8")
        leeme = rutas.personalizar / "LEEME.txt"
        if not leeme.exists():
            leeme.write_text(LEEME_PERSONALIZAR, encoding="utf-8")
    except (OSError, ValueError):
        pass
    return marca


def ruta_logo(rutas: Rutas, marca: dict) -> Path | None:
    nombre = Path(marca.get("logo") or "").name           # sólo el nombre: nunca rutas
    if not nombre or Path(nombre).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return None
    p = rutas.personalizar / nombre
    return p if p.is_file() else None
