"""Sube a la nube (Cloudflare) el reporte del día para verlo desde cualquier lado en sólo lectura.

Cada `nube_cada_minutos` (10 por defecto) y al abrir o cerrar una caja, manda el resumen de hoy (y de ayer, si cambió)
con su Excel. Si no hay internet o la nube falla, no pasa nada: se reintenta en el próximo ciclo. Nunca frena la caja.

Además hay un RELLENADO: unos minutos después de abrir el programa y cada ~6 horas, se le pregunta a la nube qué días tiene
(con su huella) y se suben, de a pocos por vez, los últimos `nube_relleno_dias` (90) que le faltan o tiene distintos. Así las vistas
de semana y mes de la web tienen datos aunque el programa haya estado apagado, y también se corrigen días viejos que se editaron.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .diario import construir_excel, datos_del_dia
from .nube_turnos import SincroTurnos
from .util import ahora_ms, dia_de

log = logging.getLogger("predio.nube")

CLAVES_RESUMEN = ("dia", "negocio", "kpis", "por_medio", "por_categoria", "por_destino", "top_productos", "fiado",
                  "movimientos_caja", "turnos_por_estado", "stock_bajo")


# Colecciones con plata: de la primera que aparezca en adelante, un día sin nada es un día "sin movimiento" y no un día que falta.
COLECCIONES_CON_PLATA = ("ventas", "cajas", "caja", "movCaja", "cc")


def huella_del_dia(resumen: dict) -> str:
    """Identifica el contenido del día para saber si la nube ya tiene lo mismo. No cuenta lo que cambia solo sin que el día cambie:
    la hora en que se armó y el stock bajo (es el stock de ahora, no el de ese día)."""
    base = {k: v for k, v in resumen.items() if k not in ("generado", "huella", "stock_bajo")}
    return hashlib.sha256(json.dumps(base, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def resumen_del_dia(d: dict) -> dict:
    resumen = {k: d[k] for k in CLAVES_RESUMEN}
    resumen["cajas"] = [{k: c.get(k) for k in ("abierta", "cerrada", "responsable", "fondo", "esperado", "contado", "diferencia", "dejado", "retiroFinal", "nota", "digital")} for c in d["cajas"]]
    resumen["cambios_importantes"] = [{"ts": c["ts"], "usuario": c["usuario"], "resumen": c["resumen"]} for c in d["cambios"] if c["importante"]][:200]
    resumen["generado"] = ahora_ms()
    resumen["huella"] = huella_del_dia(resumen)
    return resumen


def _pedido_de(d: dict, resumen: dict | None = None) -> dict:
    resumen = resumen or resumen_del_dia(d)
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "r.xlsx"
        construir_excel(d, ruta)
        xlsx = ruta.read_bytes()
    return {"dia": d["dia"], "resumen": resumen, "xlsx": base64.b64encode(xlsx).decode("ascii")}


def armar_pedido(con, dia: str) -> dict:
    return _pedido_de(datos_del_dia(con, dia))


class SubidaNube:
    # rellenado (segundos): primera vuelta, vuelta normal, vuelta cuando quedaron días por subir y vuelta después de un error
    RELLENO_ESPERA_INICIAL = 300.0
    RELLENO_CADA = 6 * 3600.0
    RELLENO_SI_QUEDAN = 180.0
    RELLENO_SI_FALLA = 1800.0
    RELLENO_POR_VUELTA = 8          # días que se suben por vez: de a pocos, para no saturar la PC ni la conexión
    RELLENO_PAUSA = 1.0             # entre un día subido y el siguiente

    def __init__(self, app) -> None:
        self.app = app
        self._despertar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._hilo_relleno: threading.Thread | None = None
        self.relleno: dict = {"ultima": None, "subidos": 0, "quedan": False, "error": None}
        self._huellas: dict[str, str] = {}
        self.ultimo_error: str | None = None
        self.ultima_subida: float | None = None
        self.turnos = SincroTurnos(app, self.llamar)

    def llamar(self, metodo: str, ruta: str, cuerpo: dict | None) -> dict:
        """Pedido a la nube con la clave de la PC. Devuelve el JSON; si falla, levanta la excepción."""
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        req = urllib.request.Request(self.url + ruta, data=datos, method=metodo,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", "User-Agent": "CajaDelPredio"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:           # noqa: S310 - la URL es https (o 127.0.0.1 en pruebas)
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                detalle = json.loads(e.read().decode("utf-8")).get("error", "")
            except (ValueError, OSError):
                detalle = ""
            raise RuntimeError(f"La nube respondió {e.code} {detalle}".strip()) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RuntimeError(f"No se pudo hablar con la nube: {e}") from None

    # ajustes
    @property
    def url(self) -> str:
        return str(self.app.ajustes.get("nube_url") or "").strip().rstrip("/")

    @property
    def token(self) -> str:
        return str(self.app.ajustes.get("nube_token") or "").strip()

    @property
    def activa(self) -> bool:
        return bool((self.url.startswith("https://") or self.url.startswith("http://127.0.0.1")) and self.token) and self.app.modo == "real"

    def iniciar(self) -> None:
        if not self.activa:
            return
        self._hilo = threading.Thread(target=self._bucle, name="nube", daemon=True)
        self._hilo.start()
        self._hilo_relleno = threading.Thread(target=self._bucle_relleno, name="nube-relleno", daemon=True)
        self._hilo_relleno.start()
        self.turnos.iniciar()

    def empujar(self) -> None:
        """Pide subir ya (por ejemplo, al cerrar la caja)."""
        self._despertar.set()

    def _bucle(self) -> None:
        cada = max(1.0, float(self.app.ajustes.get("nube_cada_minutos") or 10)) * 60
        if self.app.apagar.wait(30):
            return
        while not self.app.apagar.is_set():
            try:
                self.subir_ahora()
            except Exception:      # noqa: BLE001
                log.exception("Error inesperado al subir a la nube")
            self._despertar.wait(timeout=cada)
            if self._despertar.is_set():
                self._despertar.clear()
                if self.app.apagar.wait(5):          # junta varios cambios seguidos
                    break

    def subir_ahora(self, forzar: bool = False) -> bool:
        hoy = dia_de(ahora_ms())
        ayer = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        ok = True
        for dia in (hoy, ayer):
            con = self.app.db.lectura()
            try:
                pedido = armar_pedido(con, dia)
            finally:
                con.close()
            k = pedido["resumen"]
            huella = hashlib.sha256(json.dumps({x: k[x] for x in k if x != "generado"}, sort_keys=True, default=str).encode()).hexdigest()
            if not forzar and self._huellas.get(dia) == huella and dia != hoy:
                continue
            if not forzar and self._huellas.get(dia) == huella and self.ultima_subida and time.time() - self.ultima_subida < 3600:
                continue          # nada nuevo: se vuelve a subir como mucho cada hora para que la nube sepa que la PC está viva
            if not forzar and dia == ayer and not k["kpis"]["tickets"] and not k["cajas"]:
                continue
            ok = self._enviar(pedido) and ok
            if ok:
                self._huellas[dia] = huella
        return ok

    def estado(self) -> dict:
        return {"activa": self.activa, "url": self.url, "ultima_subida": int(self.ultima_subida * 1000) if self.ultima_subida else None, "error": self.ultimo_error,
                "turnos": self.turnos.resumen(), "relleno": dict(self.relleno)}

    # ---- rellenado de los días de atrás ----
    @property
    def relleno_dias(self) -> int:
        try:
            return max(0, int(self.app.ajustes.get("nube_relleno_dias", 90)))
        except (TypeError, ValueError):
            return 90

    def _bucle_relleno(self) -> None:
        if self.app.apagar.wait(self.RELLENO_ESPERA_INICIAL):
            return
        while not self.app.apagar.is_set():
            self.rellenar()
            espera = self.RELLENO_SI_FALLA if self.relleno["error"] else self.RELLENO_SI_QUEDAN if self.relleno["quedan"] else self.RELLENO_CADA
            if self.app.apagar.wait(espera):
                break

    def _dias_a_revisar(self) -> list[str]:
        """De ayer hacia atrás (más nuevo primero), hasta `nube_relleno_dias`, sin pasar del primer día en que el programa tuvo movimientos:
        antes de eso no hay nada que mostrar, y desde ahí un día sin ventas es un día "sin movimiento" (la nube tiene que saberlo)."""
        atras = self.relleno_dias
        if not atras:
            return []
        con = self.app.db.lectura()
        try:
            marcas = ",".join("?" * len(COLECCIONES_CON_PLATA))
            primero = con.execute(f"SELECT MIN(dia) FROM docs WHERE dia IS NOT NULL AND dia <> '' AND coleccion IN ({marcas})", COLECCIONES_CON_PLATA).fetchone()[0]
        finally:
            con.close()
        if not primero:
            return []
        hoy = dt.date.fromisoformat(dia_de(ahora_ms()))
        ayer = hoy - dt.timedelta(days=1)
        desde = max(hoy - dt.timedelta(days=atras), dt.date.fromisoformat(primero))
        return [(ayer - dt.timedelta(days=i)).isoformat() for i in range((ayer - desde).days + 1)]

    def rellenar(self, maximo: int | None = None) -> int:
        """Le pregunta a la nube qué días tiene y sube (hasta `maximo` por vez, del más nuevo al más viejo) los que le faltan o tiene distintos.
        Hoy no entra: lo sube el ciclo normal. Devuelve cuántos subió. No levanta errores: si algo falla (sin internet, nube vieja, clave mal)
        lo deja anotado en `self.relleno` y se reintenta más tarde. Nunca toca `ultima_subida` (eso es el latido del ciclo normal)."""
        maximo = self.RELLENO_POR_VUELTA if maximo is None else maximo
        subidos, quedan, error = 0, False, None
        try:
            dias = self._dias_a_revisar()
            if dias:
                nube = self.llamar("GET", f"/api/subidos?desde={dias[-1]}", None).get("dias") or {}
                for dia in dias:
                    if self.app.apagar.wait(0.02):          # cede el paso a la caja entre un día y otro
                        break
                    con = self.app.db.lectura()
                    try:
                        d = datos_del_dia(con, dia)
                    finally:
                        con.close()
                    resumen = resumen_del_dia(d)
                    if nube.get(dia) == resumen["huella"]:
                        continue
                    if subidos >= maximo:
                        quedan = True
                        break
                    self.llamar("POST", "/api/subir", _pedido_de(d, resumen))
                    nube[dia] = resumen["huella"]
                    subidos += 1
                    self.app.apagar.wait(self.RELLENO_PAUSA)
        except RuntimeError as e:
            error = str(e)
            log.warning("No se pudo completar la nube con los días de atrás: %s", e)
        except Exception as e:      # noqa: BLE001 - lo de atrás nunca puede frenar la caja
            error = f"error inesperado: {e}"
            log.exception("Error inesperado al completar la nube")
        self.relleno = {"ultima": ahora_ms(), "subidos": subidos, "quedan": quedan, "error": error}
        return subidos

    def _enviar(self, pedido: dict) -> bool:
        cuerpo = json.dumps(pedido).encode("utf-8")
        req = urllib.request.Request(self.url + "/api/subir", data=cuerpo, method="POST",
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", "User-Agent": "CajaDelPredio"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:           # noqa: S310 - la URL es https y la define quien administra
                r.read()
            self.ultimo_error = None
            self.ultima_subida = time.time()
            return True
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.ultimo_error = str(e)
            log.warning("No se pudo subir a la nube: %s", e)
            return False
