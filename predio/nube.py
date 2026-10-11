"""Sube a la nube (Cloudflare) el reporte del día para verlo desde cualquier lado en sólo lectura.

Cada `nube_cada_minutos` (10 por defecto) y al abrir o cerrar una caja, manda el resumen de hoy (y de ayer, si cambió)
con su Excel. Si no hay internet o la nube falla, no pasa nada: se reintenta en el próximo ciclo. Nunca frena la caja.
"""
from __future__ import annotations

import base64
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


def armar_pedido(con, dia: str) -> dict:
    d = datos_del_dia(con, dia)
    resumen = {k: d[k] for k in CLAVES_RESUMEN}
    resumen["cajas"] = [{k: c.get(k) for k in ("abierta", "cerrada", "responsable", "fondo", "esperado", "contado", "diferencia", "dejado", "retiroFinal", "nota", "digital")} for c in d["cajas"]]
    resumen["cambios_importantes"] = [{"ts": c["ts"], "usuario": c["usuario"], "resumen": c["resumen"]} for c in d["cambios"] if c["importante"]][:200]
    resumen["generado"] = ahora_ms()
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "r.xlsx"
        construir_excel(d, ruta)
        xlsx = ruta.read_bytes()
    return {"dia": dia, "resumen": resumen, "xlsx": base64.b64encode(xlsx).decode("ascii")}


class SubidaNube:
    def __init__(self, app) -> None:
        self.app = app
        self._despertar = threading.Event()
        self._hilo: threading.Thread | None = None
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
        import datetime as dt
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
                "turnos": self.turnos.resumen()}

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
