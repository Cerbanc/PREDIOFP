"""Servidor local: muestra la pantalla y recibe lo que se guarda.

Sólo escucha en esta PC (127.0.0.1): no se puede entrar desde la red ni desde internet.
Además cada pedido tiene que traer una clave que sólo conoce la pantalla abierta, así que otra página web
abierta en el mismo navegador no puede tocar los datos.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import VERSION, actualizador, planilla
from .almacen import Almacen, ConflictoDeRevision, DatosInvalidos
from .config import Rutas, leer_marca, ruta_logo
from .copias import Copias, CopiaInvalida
from .db import BaseDeDatos
from .diario import ArchivosDiarios, datos_del_dia
from .util import dia_de, ahora_ms, es_dia

log = logging.getLogger("predio.servidor")

MAX_JSON = 250_000_000
MAX_ARCHIVO = 40_000_000
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
IMAGEN = re.compile(r"^[0-9a-f]{20}\.(jpg|png|webp|gif)$")
TIPOS_IMAGEN = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}


@dataclass
class Aplicacion:
    rutas: Rutas
    ajustes: dict
    db: BaseDeDatos
    almacen: Almacen
    copias: Copias
    diario: ArchivosDiarios
    modo: str = "real"                  # "real" o "demo"
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    apagar: threading.Event = field(default_factory=threading.Event)
    avisos: list[str] = field(default_factory=list)
    inicio: float = field(default_factory=time.time)
    html: str = ""
    puerto: int = 0


def abrir_carpeta(ruta: Path) -> None:
    """Abre una carpeta en el Explorador de Windows."""
    if os.name == "nt":
        os.startfile(str(ruta))        # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(ruta)])
    else:
        subprocess.Popen(["xdg-open", str(ruta)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _rechazar_constante(nombre: str):
    raise ValueError(f"valor no permitido: {nombre}")


class Error(Exception):
    def __init__(self, estado: int, mensaje: str, **extra: Any):
        super().__init__(mensaje)
        self.estado, self.mensaje, self.extra = estado, mensaje, extra


def _mensaje_sqlite(e: sqlite3.Error) -> str:
    t = str(e).lower()
    if "disk is full" in t or "database or disk is full" in t:
        return "El disco está lleno: liberá espacio en la PC para poder seguir guardando."
    if "locked" in t or "busy" in t:
        return "La base de datos está ocupada. Esperá unos segundos y probá de nuevo."
    return "No se pudo escribir en la base de datos: " + str(e)


class Manejador(BaseHTTPRequestHandler):
    app: Aplicacion
    protocol_version = "HTTP/1.1"
    server_version = "CajaPredio"
    timeout = 60

    # ---- utilidades de respuesta ----
    def log_message(self, formato, *args):    # silenciar el log por pedido
        log.debug("%s %s", self.address_string(), formato % args)

    def _cabeceras_comunes(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _enviar(self, estado: int, cuerpo: bytes, tipo: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self._cabeceras_comunes()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def _json(self, estado: int, obj: Any) -> None:
        texto = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        self._enviar(estado, texto.encode("utf-8"), "application/json; charset=utf-8")

    def _leer_cuerpo(self, maximo: int) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Error(400, "Pedido inválido") from None
        if n > maximo:
            raise Error(413, "El archivo es demasiado grande")
        return self.rfile.read(n) if n else b""

    def _leer_json(self) -> dict:
        try:
            v = json.loads(self._leer_cuerpo(MAX_JSON) or b"{}", parse_constant=_rechazar_constante)
        except ValueError:
            raise Error(400, "El pedido no es un JSON válido") from None
        if not isinstance(v, dict):
            raise Error(400, "El pedido tiene que ser un objeto")
        return v

    # ---- entrada ----
    def do_GET(self):      # noqa: N802
        self._despachar("GET")

    def do_POST(self):     # noqa: N802
        self._despachar("POST")

    def do_HEAD(self):     # noqa: N802
        self._despachar("GET")

    def _despachar(self, metodo: str) -> None:
        app = self.app
        try:
            host = (self.headers.get("Host") or "").lower()
            if host not in (f"127.0.0.1:{app.puerto}", f"localhost:{app.puerto}"):
                raise Error(403, "Dirección no permitida")
            partes = urllib.parse.urlsplit(self.path)
            ruta, consulta = partes.path, urllib.parse.parse_qs(partes.query)
            if metodo == "GET" and ruta == "/":
                return self._pagina()
            if metodo == "GET" and ruta == "/favicon.ico":
                return self._enviar(204, b"", "image/x-icon")
            if metodo == "GET" and ruta == "/api/ping":
                return self._json(200, {"app": "predio", "version": VERSION, "datos": str(app.rutas.raiz), "modo": app.modo})
            if metodo == "GET" and ruta == "/marca/logo":
                return self._logo()
            if metodo == "GET" and ruta.startswith("/img/"):
                return self._imagen(ruta[5:])
            if not ruta.startswith("/api/"):
                raise Error(404, "No existe")
            origen = self.headers.get("Origin")
            if origen and origen not in (f"http://127.0.0.1:{app.puerto}", f"http://localhost:{app.puerto}"):
                raise Error(403, "Origen no permitido")
            token = self.headers.get("X-Predio-Token") or (consulta.get("t", [""])[0] if metodo == "GET" else "")
            if not secrets.compare_digest(token.encode(), app.token.encode()):
                raise Error(403, "La pantalla quedó vieja: recargala (F5).")
            manejador = self._buscar(metodo, ruta)
            if manejador is None:
                raise Error(404, "No existe")
            manejador(consulta)
        except Error as e:
            self._json(e.estado, {"ok": False, "error": e.mensaje, **e.extra})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except sqlite3.Error as e:
            log.exception("Error de base de datos")
            self._json(500, {"ok": False, "error": _mensaje_sqlite(e)})
        except Exception:      # noqa: BLE001 - la caja nunca debe caerse por un pedido
            log.exception("Error interno")
            try:
                self._json(500, {"ok": False, "error": "Error interno del programa. Quedó anotado en logs_tecnicos."})
            except Exception:  # noqa: BLE001
                pass

    def _buscar(self, metodo: str, ruta: str) -> Callable[[dict], None] | None:
        fijas: dict[tuple[str, str], Callable[[dict], None]] = {
            ("GET", "/api/estado"): self.api_estado,
            ("GET", "/api/historia"): self.api_historia,
            ("POST", "/api/borrar-historial"): self.api_borrar_historial,
            ("POST", "/api/guardar"): self.api_guardar,
            ("POST", "/api/latido"): self.api_latido,
            ("GET", "/api/salud"): self.api_salud,
            ("GET", "/api/auditoria"): self.api_auditoria,
            ("GET", "/api/copias"): self.api_copias,
            ("POST", "/api/copias/crear"): self.api_copia_crear,
            ("POST", "/api/copias/restaurar"): self.api_copia_restaurar,
            ("POST", "/api/abrir-carpeta"): self.api_abrir_carpeta,
            ("GET", "/api/planilla/plantilla.xlsx"): self.api_planilla_plantilla,
            ("GET", "/api/planilla/exportar.xlsx"): self.api_planilla_exportar,
            ("POST", "/api/planilla/leer"): self.api_planilla_leer,
            ("POST", "/api/cerrar"): self.api_cerrar,
            ("GET", "/api/actualizacion"): self.api_actualizacion,
            ("POST", "/api/actualizar"): self.api_actualizar,
        }
        if (metodo, ruta) in fijas:
            return fijas[(metodo, ruta)]
        m = re.fullmatch(r"/api/auditoria/(\d+)", ruta)
        if metodo == "GET" and m:
            return lambda q: self.api_auditoria_detalle(int(m.group(1)))
        m = re.fullmatch(r"/api/dia/(\d{4}-\d{2}-\d{2})", ruta)
        if metodo == "GET" and m:
            return lambda q: self.api_dia(m.group(1))
        return None

    # ---- páginas y archivos ----
    def _pagina(self) -> None:
        app = self.app
        marca = leer_marca(app.rutas)
        marca["logo"] = bool(ruta_logo(app.rutas, marca))
        config = json.dumps({"token": app.token, "version": VERSION, "modo": app.modo, "marca": marca}, ensure_ascii=False).replace("<", "\\u003c")
        icono = '<link rel="icon" href="/marca/logo">' if marca["logo"] else ""
        html = app.html.replace("<!--PREDIO-CONFIG-->", f"{icono}<script>window.PREDIO={config};</script>")
        self._enviar(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Content-Security-Policy": CSP})

    def _logo(self) -> None:
        app = self.app
        ruta = ruta_logo(app.rutas, leer_marca(app.rutas))
        if not ruta or ruta.stat().st_size > 5_000_000:
            raise Error(404, "No hay logo")
        tipo = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}[ruta.suffix.lower()]
        self._enviar(200, ruta.read_bytes(), tipo, {"Cache-Control": "no-cache"})

    def _imagen(self, nombre: str) -> None:
        if not IMAGEN.match(nombre):
            raise Error(404, "No existe")
        ruta = self.app.rutas.imagenes / nombre
        if not ruta.is_file():
            raise Error(404, "No existe")
        cuerpo = ruta.read_bytes()
        self._enviar(200, cuerpo, TIPOS_IMAGEN[nombre.rsplit(".", 1)[1]], {"Cache-Control": "public, max-age=31536000, immutable"})

    # ---- datos ----
    def api_estado(self, q: dict) -> None:
        txt = self.app.almacen.estado_json(int(self.app.ajustes.get("ventana_dias") or 0))
        self._json(200, txt[:-1] + ',"modo":' + json.dumps(self.app.modo) + "}")

    def api_historia(self, q: dict) -> None:
        try:
            datos = self.app.almacen.historia((q.get("desde") or [""])[0], (q.get("hasta") or [""])[0])
        except DatosInvalidos as e:
            raise Error(400, str(e)) from None
        self._json(200, {"ok": True, **datos})

    def api_borrar_historial(self, q: dict) -> None:
        cuerpo = self._leer_json()
        try:
            self.app.copias.crear("seguridad_antes_de_borrar")
        except Exception as e:      # noqa: BLE001 - sin copia previa no se borra nada
            log.exception("No se pudo hacer la copia previa al borrado")
            raise Error(500, "No se pudo hacer la copia de seguridad previa, así que no se borró nada: " + str(e)) from None
        res = self.app.almacen.borrar_historial(str(cuerpo.get("usuario") or "Caja"))
        self.app.diario.marcar([dia_de(ahora_ms())])
        self._json(200, {"ok": True, **res})

    def api_guardar(self, q: dict) -> None:
        cuerpo = self._leer_json()
        try:
            res = self.app.almacen.aplicar(cuerpo, cuerpo.get("usuario") or "Caja", cuerpo.get("base_rev"))
        except ConflictoDeRevision as e:
            raise Error(409, "Los datos cambiaron desde otra ventana. Se va a recargar la pantalla.", conflicto=True, rev=e.rev_actual) from None
        except DatosInvalidos as e:
            raise Error(400, str(e)) from None
        except (TypeError, ValueError) as e:
            raise Error(400, f"Pedido de guardado inválido: {e}") from None
        if res["cambios"]:
            self.app.diario.marcar(res["dias"])
        self._json(200, {"ok": True, "rev": res["rev"], "reemplazos": res["reemplazos"]})

    def api_latido(self, q: dict) -> None:
        self._leer_cuerpo(10_000)
        self._json(200, {"ok": True, "rev": self.app.almacen.rev(), "avisos": self.app.avisos})

    def api_salud(self, q: dict) -> None:
        app = self.app
        copias = app.copias.listar()
        try:
            libre = shutil.disk_usage(app.rutas.raiz).free
        except OSError:
            libre = None
        self._json(200, {
            "ok": True, "version": VERSION, "modo": app.modo, "carpeta": str(app.rutas.raiz), "rev": app.almacen.rev(),
            "base_bytes": app.rutas.base.stat().st_size if app.rutas.base.exists() else 0, "espacio_libre": libre,
            "copias": len(copias), "ultima_copia": copias[0] if copias else None,
            "diario": {"pendientes": app.diario.pendientes(), "error": app.diario.ultimo_error, "ultima": app.diario.ultima_generacion},
            "avisos": app.avisos, "activo_desde": app.inicio,
        })

    def api_auditoria(self, q: dict) -> None:
        def p(k, d=""):
            return (q.get(k) or [d])[0]
        try:
            filas = self.app.almacen.auditoria(p("desde") or None, p("hasta") or None, p("q"), p("importantes") == "1", int(p("limite", "300")), int(p("desplazamiento", "0")))
        except ValueError:
            raise Error(400, "Parámetros inválidos") from None
        self._json(200, {"ok": True, "filas": filas})

    def api_auditoria_detalle(self, id_: int) -> None:
        fila = self.app.almacen.detalle_auditoria(id_)
        if not fila:
            raise Error(404, "No existe")
        self._json(200, {"ok": True, "fila": fila})

    def api_dia(self, dia: str) -> None:
        con = self.app.db.lectura()
        try:
            d = datos_del_dia(con, dia)
        finally:
            con.close()
        for k in ("ventas", "ledger", "mov_caja", "stock", "turnos", "cambios", "canchas", "categorias"):
            d.pop(k, None)
        self._json(200, {"ok": True, **d})

    # ---- copias ----
    def api_copias(self, q: dict) -> None:
        self._json(200, {"ok": True, "copias": self.app.copias.listar(), "carpeta": str(self.app.rutas.copias)})

    def api_copia_crear(self, q: dict) -> None:
        self._leer_cuerpo(10_000)
        ruta = self.app.copias.crear("manual")
        self._json(200, {"ok": True, "archivo": ruta.name})

    def api_copia_restaurar(self, q: dict) -> None:
        cuerpo = self._leer_json()
        try:
            res = self.app.copias.restaurar(str(cuerpo.get("archivo") or ""), str(cuerpo.get("usuario") or "Caja"))
        except CopiaInvalida as e:
            raise Error(400, str(e)) from None
        self.app.diario.marcar([dia_de(ahora_ms())])
        self._json(200, {"ok": True, **res})

    def api_abrir_carpeta(self, q: dict) -> None:
        cuerpo = self._leer_json()
        app = self.app
        que = cuerpo.get("que")
        if que == "dia":
            dia = cuerpo.get("dia") if es_dia(cuerpo.get("dia")) else dia_de(ahora_ms())
            destino = app.diario.carpeta(dia)
            destino.mkdir(parents=True, exist_ok=True)
            app.diario.marcar([dia])
            app.diario.escribir_ahora()
        else:
            carpetas = {"datos": app.rutas.raiz, "copias": app.rutas.copias, "planillas": app.rutas.planillas,
                        "exportaciones": app.rutas.exportaciones, "imagenes": app.rutas.imagenes, "dias": app.rutas.dias}
            if que not in carpetas:
                raise Error(400, "Carpeta desconocida")
            destino = carpetas[que]
        try:
            abrir_carpeta(destino)
        except OSError as e:
            raise Error(500, f"No se pudo abrir la carpeta: {e}") from None
        self._json(200, {"ok": True, "ruta": str(destino)})

    # ---- planilla de productos y stock ----
    def _productos_y_categorias(self) -> tuple[list[dict], list[dict]]:
        with self.app.db.lock:
            filas = self.app.db.con.execute("SELECT coleccion, data FROM docs WHERE coleccion IN ('productos', 'categorias') ORDER BY seq").fetchall()
        prods = [json.loads(d) for c, d in filas if c == "productos"]
        cats = [json.loads(d) for c, d in filas if c == "categorias"]
        return prods, cats

    def _descarga(self, datos: bytes, nombre: str, copia_en: Path | None = None) -> None:
        if copia_en is not None:
            try:
                copia_en.mkdir(parents=True, exist_ok=True)
                (copia_en / nombre).write_bytes(datos)
            except OSError:
                log.warning("No se pudo guardar una copia de %s en %s", nombre, copia_en)
        self._enviar(200, datos, XLSX, {"Content-Disposition": f'attachment; filename="{nombre}"'})

    def api_planilla_plantilla(self, q: dict) -> None:
        prods, cats = self._productos_y_categorias()
        self._descarga(planilla.construir_planilla(prods, cats, con_datos=False), "plantilla_productos_y_stock.xlsx", self.app.rutas.planillas)

    def api_planilla_exportar(self, q: dict) -> None:
        prods, cats = self._productos_y_categorias()
        nombre = f"productos_y_stock_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
        self._descarga(planilla.construir_planilla(prods, cats, con_datos=True), nombre, self.app.rutas.planillas)

    def api_planilla_leer(self, q: dict) -> None:
        datos = self._leer_cuerpo(MAX_ARCHIVO)
        nombre = urllib.parse.unquote(self.headers.get("X-Nombre") or "planilla.xlsx")
        try:
            self._json(200, {"ok": True, **planilla.leer_planilla(datos, nombre)})
        except planilla.PlanillaInvalida as e:
            raise Error(400, str(e)) from None

    def api_actualizacion(self, q: dict) -> None:
        """¿Hay una versión nueva? Se consulta a GitHub sólo si hace falta (se recuerda 6 horas, salvo que se pida de nuevo)."""
        app = self.app
        forzar = (q.get("forzar") or ["0"])[0] == "1"
        previo = getattr(app, "_actualizacion", None)
        if forzar or previo is None or time.time() - previo[0] > 6 * 3600:
            previo = (time.time(), actualizador.buscar(str(app.ajustes.get("actualizaciones_repo") or "")))
            app._actualizacion = previo      # type: ignore[attr-defined]
        self._json(200, {"ok": True, **previo[1]})

    def api_actualizar(self, q: dict) -> None:
        """Baja el instalador, hace una copia de seguridad y pide cerrar el programa: el instalador se ejecuta cuando ya cerró."""
        self._leer_cuerpo(10_000)
        app = self.app
        info = actualizador.buscar(str(app.ajustes.get("actualizaciones_repo") or ""))
        if not info.get("hay"):
            raise Error(400, info.get("error") or "Ya tenés la última versión.")
        try:
            ruta = actualizador.descargar(info, app.rutas.raiz / "actualizaciones")
            app.copias.crear("seguridad_antes_de_actualizar")
        except actualizador.ErrorActualizacion as e:
            raise Error(502, str(e)) from None
        app.instalador_pendiente = ruta                  # type: ignore[attr-defined]
        self._json(200, {"ok": True, "version": info["version"]})
        app.apagar.set()

    def api_cerrar(self, q: dict) -> None:
        self._leer_cuerpo(10_000)
        self._json(200, {"ok": True})
        self.app.apagar.set()


class Servidor:
    def __init__(self, app: Aplicacion, puerto: int, intentos: int = 10):
        handler = type("ManejadorApp", (Manejador,), {"app": app})
        ThreadingHTTPServer.allow_reuse_address = os.name != "nt"     # en Windows reusar el puerto permitiría dos instancias
        ThreadingHTTPServer.daemon_threads = True
        ultimo: OSError | None = None
        for i in range(intentos if puerto else 1):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", puerto + i if puerto else 0), handler)
                break
            except OSError as e:
                ultimo = e
        else:
            raise OSError(f"No se pudo abrir ningún puerto entre {puerto} y {puerto + intentos - 1}: {ultimo}")
        self.puerto = self.httpd.server_address[1]
        app.puerto = self.puerto
        self.app = app
        self.hilo = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, name="servidor", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.puerto}/"

    def iniciar(self) -> None:
        self.hilo.start()

    def parar(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.hilo.join(timeout=5)
