"""Sincroniza los turnos entre el programa y la nube (donde también los cargan desde el celular).

- Los cambios hechos en el programa suben a la nube; los hechos en la nube bajan al programa. Cada registro lleva la versión
  (`modificado`) sobre la que se basó el cambio; si dos lados cambiaron lo mismo, gana la nube, salvo que el turno ya esté en una
  cuenta o con pagos en la caja de la PC (eso sólo se maneja acá y la nube no deja tocarlo).
- La cancha se identifica por su `clave` (f5a, f5b, f7, padelT, padelA), que es igual en los dos lados.
- Lo que cobra la caja (pagos, ticket) nunca baja desde la nube: sube sólo como dato (`sena`, `bloqueado`).
- Si no hay internet no pasa nada: se reintenta en el próximo ciclo. Nunca frena la caja.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any

from .util import ahora_ms

log = logging.getLogger("predio.turnos")

COLECCIONES = ("turnos", "fijos", "canchas")
ESTADOS = ("reservado", "cobrado", "cancelado")


def _huella(d: dict) -> str:
    return hashlib.sha1(json.dumps(d, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]


class SincroTurnos:
    def __init__(self, app, llamar) -> None:
        self.app = app
        self._llamar = llamar                 # función (metodo, ruta, cuerpo) -> dict; la pone SubidaNube
        self._lock = threading.Lock()
        self._despertar = threading.Event()
        self._hilo: threading.Thread | None = None
        self.ultima_ok: int | None = None
        self.error: str | None = None
        self.problemas: list[str] = []
        self.hubo_cambios_locales = 0          # cuántos registros bajaron de la nube en la última vuelta

    # ---- estado guardado en disco ----
    @property
    def _archivo(self):
        return self.app.rutas.datos / "nube_turnos.json"

    def _cargar(self) -> dict:
        try:
            st = json.loads(self._archivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        st.setdefault("inst", os.urandom(3).hex())
        for k in ("map_t", "map_f", "vistos_t", "vistos_f"):
            st.setdefault(k, {})
        st.setdefault("cursor", 0)
        st.setdefault("canchas_h", "")
        return st

    def _guardar(self, st: dict) -> None:
        tmp = self._archivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, self._archivo)

    # ---- ciclo ----
    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._bucle, name="nube-turnos", daemon=True)
        self._hilo.start()

    def empujar(self) -> None:
        self._despertar.set()

    def _bucle(self) -> None:
        cada = max(10.0, float(self.app.ajustes.get("nube_turnos_cada_segundos") or 30))
        if self.app.apagar.wait(15):
            return
        while not self.app.apagar.is_set():
            self.sincronizar()
            self._despertar.wait(timeout=cada)
            if self._despertar.is_set():
                self._despertar.clear()
                if self.app.apagar.wait(3):
                    break

    def estado(self) -> dict:
        return {"ultima_ok": self.ultima_ok, "error": self.error, "problemas": list(self.problemas)}

    # ---- lectura de lo local ----
    def _leer_local(self) -> dict:
        con = self.app.db.lectura()
        try:
            def todo(col):
                return {r[0]: json.loads(r[1]) for r in con.execute("SELECT id, data FROM docs WHERE coleccion = ? ORDER BY seq", (col,))}
            out = {c: todo(c) for c in COLECCIONES}
            en_cuenta: set[str] = set()
            for r in con.execute("SELECT data FROM docs WHERE coleccion = 'comandas'"):
                for it in (json.loads(r[0]).get("items") or []):
                    if isinstance(it, dict) and it.get("turnoId"):
                        en_cuenta.add(it["turnoId"])
            out["en_cuenta"] = en_cuenta
            return out
        finally:
            con.close()

    # ---- traducciones ----
    @staticmethod
    def _clave_de(local: dict, cancha_id: str) -> str | None:
        c = local["canchas"].get(cancha_id)
        return c.get("clave") if c else None

    @staticmethod
    def _local_de_clave(local: dict, clave: str) -> str | None:
        for cid, c in local["canchas"].items():
            if c.get("clave") == clave:
                return cid
        return None

    def _id_nube(self, st: dict, mapa: str, lid: str) -> str:
        return st[mapa].get(lid) or f"{st['inst']}-{lid}"

    def _rep_turno(self, st: dict, local: dict, t: dict) -> dict | None:
        clave = self._clave_de(local, t.get("canchaId"))
        if not clave:
            return None
        pagos = sum(float(p.get("monto") or 0) for p in (t.get("pagos") or []))
        estado = t.get("estado") if t.get("estado") in ESTADOS else "reservado"
        fijo = t.get("fijoId")
        return {
            "id": self._id_nube(st, "map_t", t["id"]), "cancha_id": clave, "fecha": t.get("fecha"), "ini": t.get("ini"), "fin": t.get("fin"),
            "cliente": t.get("cliente") or "", "tel": t.get("tel") or "", "nota": t.get("nota") or "", "precio": t.get("precio") or 0,
            "sena": int(pagos), "estado": estado, "tipo_precio": "fijo" if t.get("tipoPrecio") == "fijo" else "comun",
            "fijo_id": self._id_nube(st, "map_f", fijo) if fijo else None, "ocurrencia": t.get("ocurrencia"),
            "bloqueado": 1 if (estado == "cobrado" or pagos > 0 or t["id"] in local["en_cuenta"]) else 0,
        }

    def _rep_fijo(self, st: dict, local: dict, f: dict) -> dict | None:
        clave = self._clave_de(local, f.get("canchaId"))
        if not clave:
            return None
        return {"id": self._id_nube(st, "map_f", f["id"]), "cancha_id": clave, "dow": f.get("dow"), "ini": f.get("ini"), "cliente": f.get("cliente") or "",
                "tel": f.get("tel") or "", "nota": f.get("nota") or "", "desde": f.get("desde"), "hasta": f.get("hasta") or None}

    def _rep_cancha(self, c: dict, orden: int) -> dict:
        return {"id": c.get("clave"), "nombre": c.get("nombre"), "deporte": c.get("deporte"), "dur": c.get("dur"), "ap": c.get("ap"), "ci": c.get("ci"),
                "precio": c.get("precio"), "precio_fijo": c.get("precioFijo"), "orden": orden}

    def _turno_desde_nube(self, st: dict, local: dict, rec: dict, lid: str, existente: dict | None) -> dict | None:
        cid = self._local_de_clave(local, rec["cancha_id"])
        if not cid:
            return None
        fijo_local = None
        if rec.get("fijo_id"):
            fijo_local = next((k for k, v in st["map_f"].items() if v == rec["fijo_id"]), rec["fijo_id"])
        base = dict(existente) if existente else {"pagos": [], "ventaId": None, "clienteId": None}
        estado = rec.get("estado") if rec.get("estado") in ESTADOS else "reservado"
        if existente and existente.get("estado") == "cobrado":
            estado = "cobrado"
        base.update({"id": lid, "canchaId": cid, "fecha": rec["fecha"], "ini": rec["ini"], "fin": rec["fin"], "cliente": rec.get("cliente") or "",
                     "tel": rec.get("tel") or "", "nota": rec.get("nota") or "", "precio": rec.get("precio") or 0, "estado": estado,
                     "esFijo": bool(rec.get("fijo_id")), "fijoId": fijo_local, "ocurrencia": rec.get("ocurrencia"),
                     "tipoPrecio": "fijo" if rec.get("tipo_precio") == "fijo" else "comun"})
        return base

    def _fijo_desde_nube(self, st: dict, local: dict, rec: dict, lid: str, existente: dict | None) -> dict | None:
        cid = self._local_de_clave(local, rec["cancha_id"])
        if not cid:
            return None
        base = dict(existente) if existente else {"clienteId": None}
        base.update({"id": lid, "canchaId": cid, "dow": rec["dow"], "ini": rec["ini"], "cliente": rec.get("cliente") or "", "tel": rec.get("tel") or "",
                     "nota": rec.get("nota") or "", "desde": rec["desde"], "hasta": rec.get("hasta") or None})
        return base

    # ---- una vuelta completa ----
    def sincronizar(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self._vuelta()
            self.error = None
            self.ultima_ok = ahora_ms()
            return True
        except Exception as e:      # noqa: BLE001 - nunca debe frenar la caja
            self.error = str(e)[:300]
            log.warning("No se pudo sincronizar los turnos: %s", e)
            return False
        finally:
            self._lock.release()

    def _vuelta(self) -> None:
        st = self._cargar()
        local = self._leer_local()
        ops: list[dict] = []

        # 1) lo que cambió en la nube
        r = self._llamar("GET", f"/api/sync?desde={int(st['cursor'])}", None)
        cursor = int(st["cursor"])
        bajados = 0
        for rec in r.get("fijos", []):
            cursor = max(cursor, int(rec["modificado"]))
            lid = next((k for k, v in st["map_f"].items() if v == rec["id"]), rec["id"])
            ex = local["fijos"].get(lid)
            vis = st["vistos_f"].get(lid)
            if ex is not None and vis and vis["mod"] == rec["modificado"]:
                continue
            nuevo = self._fijo_desde_nube(st, local, rec, lid, ex)
            if nuevo is None:
                continue
            st["map_f"][lid] = rec["id"]
            local["fijos"][lid] = nuevo
            ops.append({"c": "fijos", "id": lid, "d": nuevo})
            st["vistos_f"][lid] = {"mod": rec["modificado"], "h": _huella(self._rep_fijo(st, local, nuevo) or {})}
            bajados += 1
        for rec in r.get("turnos", []):
            cursor = max(cursor, int(rec["modificado"]))
            lid = next((k for k, v in st["map_t"].items() if v == rec["id"]), rec["id"])
            ex = local["turnos"].get(lid)
            vis = st["vistos_t"].get(lid)
            if ex is not None and vis and vis["mod"] == rec["modificado"]:
                continue
            if ex is not None and vis:
                rep_local = self._rep_turno(st, local, ex)
                cambio_local = rep_local is not None and _huella(rep_local) != vis["h"]
                if cambio_local and rep_local["bloqueado"]:
                    # el turno ya está en una cuenta o con pagos acá: manda la PC. Se sube su versión sobre la nueva de la nube
                    st["vistos_t"][lid] = {"mod": rec["modificado"], "h": "pendiente"}
                    continue
            nuevo = self._turno_desde_nube(st, local, rec, lid, ex)
            if nuevo is None:
                continue
            st["map_t"][lid] = rec["id"]
            local["turnos"][lid] = nuevo
            ops.append({"c": "turnos", "id": lid, "d": nuevo})
            st["vistos_t"][lid] = {"mod": rec["modificado"], "h": _huella(self._rep_turno(st, local, nuevo) or {})}
            bajados += 1
        st["cursor"] = cursor

        if ops:
            self.app.almacen.aplicar({"ops": ops, "formas": {"turnos": "lista", "fijos": "lista"}}, "Nube (celular)")
            self.app.diario.marcar(sorted({o["d"].get("fecha") for o in ops if o["c"] == "turnos" and o["d"].get("fecha")}))
        self.hubo_cambios_locales = bajados

        # 2) lo que cambió acá
        envio: dict[str, Any] = {"canchas": [], "fijos": [], "turnos": []}
        reps_c = [self._rep_cancha(c, i + 1) for i, c in enumerate(local["canchas"].values()) if c.get("clave")]
        h_c = _huella(reps_c)
        if h_c != st["canchas_h"]:
            envio["canchas"] = reps_c
        for lid, f in local["fijos"].items():
            rep = self._rep_fijo(st, local, f)
            vis = st["vistos_f"].get(lid)
            if rep is not None and (vis is None or vis["h"] != _huella(rep)):
                envio["fijos"].append({**rep, "base_mod": vis["mod"] if vis else None, "_l": lid})
        limite = (dt.date.today() - dt.timedelta(days=7)).isoformat()
        for lid, t in local["turnos"].items():
            rep = self._rep_turno(st, local, t)
            vis = st["vistos_t"].get(lid)
            if rep is None or (vis is not None and vis["h"] == _huella(rep)):
                continue
            if vis is None and (t.get("fecha") or "") < limite:
                continue                                           # historia vieja: la nube no la necesita
            envio["turnos"].append({**rep, "base_mod": vis["mod"] if vis else None, "_l": lid})
        problemas: list[str] = []
        if envio["canchas"] or envio["fijos"] or envio["turnos"]:
            resp = self._llamar("POST", "/api/sync", {k: [{kk: vv for kk, vv in x.items() if kk != "_l"} for x in v] for k, v in envio.items()})
            if envio["canchas"]:
                st["canchas_h"] = h_c
            por_id = {x["_l"]: x for x in envio["fijos"]}
            for res in resp.get("fijos", []):
                lid = next((k for k, x in por_id.items() if x["id"] == res["id"]), None)
                if lid and res.get("ok"):
                    st["map_f"][lid] = res["id"]
                    st["vistos_f"][lid] = {"mod": res["mod"], "h": _huella(self._rep_fijo(st, local, local["fijos"][lid]) or {})}
            por_id = {x["_l"]: x for x in envio["turnos"]}
            for res in resp.get("turnos", []):
                lid = next((k for k, x in por_id.items() if x["id"] == res["id"]), None)
                if not lid:
                    continue
                if res.get("ok"):
                    st["map_t"][lid] = res["id"]
                    st["vistos_t"][lid] = {"mod": res["mod"], "h": _huella(self._rep_turno(st, local, local["turnos"][lid]) or {})}
                elif res.get("conflicto") == "choque":
                    t = local["turnos"][lid]
                    problemas.append(f"El turno de {t.get('cliente') or 'sin nombre'} del {t.get('fecha', '')[8:]}/{t.get('fecha', '')[5:7]} choca con otro cargado desde el celular: {res.get('con', '')}. Revisalo en Turnos.")
        self.problemas = problemas
        self._guardar(st)
        if ops or envio["turnos"] or envio["fijos"]:       # cambió algo de los turnos: se actualiza también la planilla Excel de la web
            nube = getattr(self.app, "nube", None)
            if nube is not None:
                try:
                    nube.subir_agendas()
                except Exception as e:      # noqa: BLE001
                    log.warning("No se pudieron subir las planillas de turnos: %s", e)

    # ---- para la pantalla ----
    def resumen(self) -> dict:
        return {**self.estado(), "bajados": self.hubo_cambios_locales}
