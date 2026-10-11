import datetime as dt
import json
import threading
from types import SimpleNamespace

from conftest import base_inicial, op
from predio.nube_turnos import SincroTurnos

F = (dt.date.today() + dt.timedelta(days=3)).isoformat()


def cancha(i, clave, nombre, dur=60, ap=600):
    return op("canchas", i, {"id": i, "clave": clave, "nombre": nombre, "deporte": "futbol", "dur": dur, "ap": ap, "ci": 1440, "precio": 28000, "precioFijo": 24000, "lugarId": None})


def turno(id_, cancha_id, ini, cliente="X", pagos=None, estado="reservado"):
    return op("turnos", id_, {"id": id_, "canchaId": cancha_id, "fecha": F, "ini": ini, "fin": ini + 60, "cliente": cliente, "tel": "", "nota": "", "precio": 28000,
                              "estado": estado, "pagos": pagos or [], "ventaId": None, "esFijo": False, "fijoId": None, "clienteId": None, "tipoPrecio": "comun"})


class NubeFalsa:
    """Hace de nube: devuelve lo que se le cargó y anota lo que la PC le manda."""
    def __init__(self):
        self.turnos, self.recibido, self.respuesta_push = [], [], None

    def __call__(self, metodo, ruta, cuerpo):
        if metodo == "GET":
            return {"turnos": self.turnos, "fijos": [], "canchas": [], "ahora": 1}
        self.recibido.append(cuerpo)
        ids = [t["id"] for t in cuerpo.get("turnos", [])]
        return self.respuesta_push or {"ok": True, "canchas": 0, "fijos": [], "turnos": [{"id": i, "ok": True, "mod": 100 + n} for n, i in enumerate(ids)]}


def montar(almacen, db, rutas, extra=()):
    almacen.aplicar(base_inicial(), "Caja", 0)
    almacen.aplicar({"formas": {"canchas": "lista", "turnos": "lista", "fijos": "lista", "comandas": "mapa"},
                     "ops": [cancha("cn1", "f5a", "Fútbol 5 · Cancha 1"), cancha("cn2", "f5b", "Fútbol 5 · Cancha 2"), *extra]}, "Caja", None)
    app = SimpleNamespace(db=db, almacen=almacen, diario=SimpleNamespace(marcar=lambda d: None), rutas=rutas, ajustes={}, modo="real", apagar=threading.Event())
    nube = NubeFalsa()
    return SincroTurnos(app, nube), nube, almacen


def docs(db, col):
    con = db.lectura()
    try:
        return {r[0]: json.loads(r[1]) for r in con.execute("SELECT id, data FROM docs WHERE coleccion = ?", (col,))}
    finally:
        con.close()


def test_un_turno_cargado_desde_el_celular_aparece_en_el_programa(almacen, db, rutas):
    s, nube, _ = montar(almacen, db, rutas)
    nube.turnos = [{"id": "tweb1", "cancha_id": "f5b", "fecha": F, "ini": 1200, "fin": 1260, "cliente": "Del celu", "tel": "11", "nota": "", "precio": 28000, "sena": 0,
                    "estado": "reservado", "tipo_precio": "comun", "fijo_id": None, "ocurrencia": None, "bloqueado": 0, "modificado": 50}]
    assert s.sincronizar() and s.error is None
    t = docs(db, "turnos")["tweb1"]
    assert t["canchaId"] == "cn2" and t["cliente"] == "Del celu" and t["pagos"] == [] and t["estado"] == "reservado"
    assert all(not r["turnos"] for r in nube.recibido)          # lo que bajó no vuelve a subir (sólo suben las canchas, una vez)
    n = len(nube.recibido)
    assert s.sincronizar() and len(nube.recibido) == n


def test_lo_que_se_carga_en_el_programa_sube_y_las_senias_son_un_dato(almacen, db, rutas):
    s, nube, al = montar(almacen, db, rutas, [turno("t1", "cn1", 1200, "Los Pibes", pagos=[{"id": "p", "monto": 5000, "nombre": "Efectivo", "metodoId": "mp1"}])])
    assert s.sincronizar()
    enviado = nube.recibido[0]["turnos"][0]
    assert enviado["cancha_id"] == "f5a" and enviado["sena"] == 5000 and enviado["bloqueado"] == 1 and enviado["base_mod"] is None
    assert enviado["id"].endswith("-t1") and nube.recibido[0]["canchas"][0]["id"] == "f5a"
    nube.recibido.clear()
    al.aplicar({"ops": [turno("t1", "cn1", 1260, "Los Pibes", pagos=[{"id": "p", "monto": 5000, "nombre": "Efectivo", "metodoId": "mp1"}])]}, "Caja", None)
    assert s.sincronizar()
    assert nube.recibido[0]["turnos"][0]["base_mod"] == 100 and nube.recibido[0]["turnos"][0]["ini"] == 1260      # sube sólo lo que cambió, sobre la versión que la nube tenía


def test_un_turno_en_una_cuenta_queda_bloqueado_para_la_web(almacen, db, rutas):
    s, nube, _ = montar(almacen, db, rutas, [turno("t1", "cn1", 1200), op("comandas", "m1", {"items": [{"turnoId": "t1", "cant": 1, "nota": ""}]})])
    assert s.sincronizar()
    assert nube.recibido[0]["turnos"][0]["bloqueado"] == 1


def test_si_cambian_los_dos_lados_gana_la_nube_salvo_que_este_en_la_caja(almacen, db, rutas):
    s, nube, al = montar(almacen, db, rutas, [turno("t1", "cn1", 1200, "Viejo"), turno("t2", "cn2", 1200, "Cobrado")])
    assert s.sincronizar()
    ids = {t["id"]: t["id"] for t in nube.recibido[0]["turnos"]}
    nube.recibido.clear()
    al.aplicar({"ops": [turno("t1", "cn1", 1200, "Cambio local"), turno("t2", "cn2", 1200, "Cobrado local", estado="cobrado")]}, "Caja", None)
    base = {"cancha_id": "f5a", "fecha": F, "ini": 1200, "fin": 1260, "tel": "", "nota": "", "precio": 28000, "sena": 0, "estado": "reservado", "tipo_precio": "comun",
            "fijo_id": None, "ocurrencia": None, "bloqueado": 0}
    nube.turnos = [{**base, "id": ids["" + next(i for i in ids if i.endswith("-t1"))], "cliente": "Cambio web", "modificado": 200},
                   {**base, "id": next(i for i in ids if i.endswith("-t2")), "cancha_id": "f5b", "cliente": "Cambio web 2", "modificado": 201}]
    assert s.sincronizar()
    d = docs(db, "turnos")
    assert d["t1"]["cliente"] == "Cambio web"                      # gana la nube
    assert d["t2"]["cliente"] == "Cobrado local" and d["t2"]["estado"] == "cobrado"      # ya cobrado acá: manda la PC
    assert [t["id"] for t in nube.recibido[0]["turnos"]] == [next(i for i in ids if i.endswith("-t2"))]
    assert nube.recibido[0]["turnos"][0]["base_mod"] == 201       # y se sube sobre la última versión de la nube


def test_un_choque_se_avisa_y_se_reintenta(almacen, db, rutas):
    s, nube, _ = montar(almacen, db, rutas, [turno("t1", "cn1", 1200, "Choca")])
    nube.respuesta_push = {"ok": True, "canchas": 0, "fijos": [], "turnos": [{"id": "x", "conflicto": "choque", "con": "Fútbol 7 (Otro)"}]}
    s.sincronizar()
    nube.respuesta_push = {"ok": True, "canchas": 0, "fijos": [], "turnos": [{"id": nube.recibido[0]["turnos"][0]["id"], "conflicto": "choque", "con": "Fútbol 7 (Otro)"}]}
    s.sincronizar()
    assert s.problemas and "Choca" in s.problemas[0] and "Fútbol 7 (Otro)" in s.problemas[0]
    assert len(nube.recibido) >= 2 and nube.recibido[-1]["turnos"][0]["id"] == nube.recibido[0]["turnos"][0]["id"]      # no se da por sincronizado


def test_sin_internet_no_rompe(almacen, db, rutas):
    s, nube, _ = montar(almacen, db, rutas, [turno("t1", "cn1", 1200)])
    def falla(*a):
        raise RuntimeError("No se pudo hablar con la nube")
    s._llamar = falla
    assert s.sincronizar() is False and "nube" in s.error
