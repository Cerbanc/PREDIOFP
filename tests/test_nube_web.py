"""Turnos en la web (nube): búsqueda, semana, quién cargó cada turno, choques entre celulares y sincronización con la PC.

Levanta una copia local de la nube (el Worker real con wrangler + una base D1 simulada, ver nube_local.py) en un puerto libre.
Se saltea sola si falta wrangler o node. Cada prueba usa sus propias fechas y nombres, así que comparten la misma nube sin pisarse.
"""
import datetime as dt
import json
import threading
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from conftest import base_inicial, op
from nube_local import CLAVE_PC, RAIZ, NubeLocal, disponible
from predio.nube import SubidaNube

pytestmark = pytest.mark.skipif(not disponible(), reason="falta wrangler (/tmp/w/node_modules/.bin/wrangler) o node")

# horarios nuevos de las canchas: fútbol 14:00 a 23:00 de a 60 min; pádel 10:00 a 23:30 de a 90 min
CANCHAS = [
    {"id": "f5a", "nombre": "Fútbol 5 · Cancha 1", "deporte": "futbol", "dur": 60, "ap": 840, "ci": 1380, "precio": 28000, "precio_fijo": 24000, "orden": 1},
    {"id": "f5b", "nombre": "Fútbol 5 · Cancha 2", "deporte": "futbol", "dur": 60, "ap": 840, "ci": 1380, "precio": 28000, "precio_fijo": 24000, "orden": 2},
    {"id": "f7", "nombre": "Fútbol 7 · Cancha completa", "deporte": "futbol", "dur": 60, "ap": 840, "ci": 1380, "precio": 48000, "precio_fijo": 42000, "orden": 3},
    {"id": "padelT", "nombre": "Pádel · Techada", "deporte": "padel", "dur": 90, "ap": 600, "ci": 1410, "precio": 26000, "precio_fijo": 22000, "orden": 4},
    {"id": "padelA", "nombre": "Pádel · Abierta", "deporte": "padel", "dur": 90, "ap": 600, "ci": 1410, "precio": 22000, "precio_fijo": 19000, "orden": 5},
]
H = lambda h, m=0: h * 60 + m  # noqa: E731


def hoy_ar() -> dt.date:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)).date()


def fecha(n: int) -> str:
    return (hoy_ar() + dt.timedelta(days=n)).isoformat()


def lunes(f: str) -> str:
    d = dt.date.fromisoformat(f)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


@pytest.fixture(scope="module")
def nube(tmp_path_factory):
    n = NubeLocal(tmp_path_factory.mktemp("nube")).iniciar()
    try:
        assert n.api("POST", "/api/sync", {"canchas": CANCHAS}, pc=True)[0] == 200
        yield n
    finally:
        n.parar()


def fila(nube, f, cliente):
    return next(t for t in nube.turnos(f) if t["cliente"] == cliente)


# ------------------------------------------------------------------ horarios y regla de Fútbol 7

def test_los_horarios_salen_de_las_canchas_y_no_estan_escritos_en_el_codigo(nube):
    f = fecha(400)
    assert [c["id"] for c in nube.api("GET", f"/api/turnos?fecha={f}")[1]["canchas"]] == ["f5a", "f5b", "f7", "padelT", "padelA"]
    for cancha, ini in [("f5a", H(14)), ("f5a", H(22)), ("padelT", H(10)), ("padelT", H(11, 30)), ("padelT", H(22)), ("padelA", H(20, 30))]:
        assert nube.reservar(cancha, f, ini, "Horario ok")[0] == 200, (cancha, ini)
    for cancha, ini in [("f5b", H(13)), ("f5b", H(23)), ("f5b", H(14, 30)), ("padelA", H(10, 30)), ("padelA", H(23, 30)), ("padelA", H(9))]:
        st, r = nube.reservar(cancha, f, ini, "Horario malo")
        assert st == 400 and "no existe" in r["error"], (cancha, ini, r)
    # si el programa de la PC cambia los horarios, la web los toma enseguida
    nube.api("POST", "/api/sync", {"canchas": [{**CANCHAS[1], "ap": H(15), "dur": 90, "ci": H(21)}]}, pc=True)
    try:
        assert nube.reservar("f5b", f, H(14))[0] == 400
        assert nube.reservar("f5b", f, H(16, 30), "Nuevo horario")[0] == 200
    finally:
        nube.api("POST", "/api/sync", {"canchas": [CANCHAS[1]]}, pc=True)


def test_futbol_7_contra_los_dos_futbol_5_en_los_dos_sentidos(nube):
    f = fecha(401)
    assert nube.reservar("f5a", f, H(20), "Uno")[0] == 200
    st, r = nube.reservar("f7", f, H(20), "Siete")
    assert st == 409 and "Fútbol 5 · Cancha 1" in r["error"]                       # hay un F5: no hay F7
    assert nube.reservar("f5b", f, H(20), "Dos")[0] == 200                         # el otro F5 sigue libre
    assert nube.reservar("f7", f, H(21), "Siete")[0] == 200
    for c in ("f5a", "f5b"):
        st, r = nube.reservar(c, f, H(21), "Choca")
        assert st == 409 and "Fútbol 7" in r["error"]                              # hay F7: no hay F5 (ninguno de los dos)
    assert nube.reservar("padelT", f, H(20, 30), "Pádel no depende")[0] == 200
    # al liberar el F5 queda libre el F7 sólo cuando se liberaron los dos
    nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": fila(nube, f, "Uno")["id"], "quien": "Ana"})
    assert nube.reservar("f7", f, H(20), "Siete bis")[0] == 409
    nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": fila(nube, f, "Dos")["id"], "quien": "Ana"})
    assert nube.reservar("f7", f, H(20), "Siete bis")[0] == 200
    # reprogramar también respeta la regla
    nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": fila(nube, f, "Siete bis")["id"], "quien": "Ana"})
    assert nube.reservar("f5a", f, H(19), "Se mueve")[0] == 200
    st, r = nube.api("POST", "/api/turnos", {"accion": "mover", "id": fila(nube, f, "Se mueve")["id"], "cancha": "f5a", "fecha": f, "ini": H(21), "quien": "Ana"})
    assert st == 409 and "Fútbol 7" in r["error"]


def test_dos_celulares_reservando_lo_mismo_uno_solo_gana(nube):
    for ronda in range(5):
        f = fecha(410 + ronda)
        pedidos = [(["f5a", "f7", "f5b"][i % 3], "ABCDEFGHIJKL"[i]) for i in range(12)]      # doce celulares tocando «Reservar» en el mismo instante
        res: dict[str, int] = {}
        barrera = threading.Barrier(len(pedidos))

        def ir(cancha, nombre):
            barrera.wait()
            res[nombre] = nube.reservar(cancha, f, H(20), "Cliente " + nombre, quien="Cel " + nombre)[0]

        hilos = [threading.Thread(target=ir, args=p) for p in pedidos]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        assert set(res.values()) <= {200, 409}, res
        ganadores = [t for t in nube.turnos(f) if t["ini"] == H(20)]
        por_cancha = [t["cancha_id"] for t in ganadores]
        assert len(por_cancha) == len(set(por_cancha)), f"dos turnos en la misma cancha: {por_cancha}"
        assert not ("f7" in por_cancha and ({"f5a", "f5b"} & set(por_cancha))), f"F7 junto con F5: {por_cancha}"
        assert sum(1 for v in res.values() if v == 200) == len(ganadores)
        assert ganadores, "alguien tenía que ganar"


def test_dos_fijos_a_la_vez_en_el_mismo_horario_uno_solo_gana(nube):
    f = fecha(430)
    res = []
    barrera = threading.Barrier(3)

    def ir(n):
        barrera.wait()
        res.append(nube.reservar("padelA", f, H(13), "Fijo " + n, fijo=True, quien=n)[0])

    hilos = [threading.Thread(target=ir, args=(n,)) for n in "ABC"]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert sorted(res) == [200, 409, 409], res
    ocurrencias = [t for t in nube.turnos(f) if t["cancha_id"] == "padelA" and t["ini"] == H(13)]
    assert len(ocurrencias) == 1 and ocurrencias[0]["virtual"]


# ------------------------------------------------------------------ quién cargó y quién cambió

def test_se_anota_quien_cargo_y_quien_cambio_cada_turno(nube):
    f = fecha(440)
    assert nube.reservar("f5a", f, H(17), "Los Quién", "11 2222-3333", quien="Ana")[0] == 200
    t = fila(nube, f, "Los Quién")
    assert t["creado_por"] == "Ana" and t["modificado_por"] == "Ana"
    assert t["creado"] > 0 and t["modificado"] == t["creado"] == t["modificado_en"]

    st, _ = nube.api("POST", "/api/turnos", {"accion": "editar", "id": t["id"], "cliente": "Los Quién", "tel": "11 2222-3333", "nota": "con seña", "quien": "Beto"})
    assert st == 200
    t2 = fila(nube, f, "Los Quién")
    assert t2["creado_por"] == "Ana" and t2["modificado_por"] == "Beto" and t2["nota"] == "con seña"
    assert t2["creado"] == t["creado"] and t2["modificado_en"] == t2["modificado"] >= t["modificado"]

    nube.api("POST", "/api/turnos", {"accion": "mover", "id": t["id"], "cancha": "f5a", "fecha": f, "ini": H(18), "quien": "Carla"})
    t3 = fila(nube, f, "Los Quién")
    assert t3["ini"] == H(18) and t3["creado_por"] == "Ana" and t3["modificado_por"] == "Carla"

    # un fijo: lo cargó Ana, lo editó Beto; las ocurrencias de cada semana lo muestran
    assert nube.reservar("padelT", f, H(16), "Fijo Quién", fijo=True, quien="Ana")[0] == 200
    prox = (dt.date.fromisoformat(f) + dt.timedelta(days=14)).isoformat()
    o = fila(nube, prox, "Fijo Quién")
    assert o["virtual"] and o["creado_por"] == "Ana" and o["creado"] > 0
    nube.api("POST", "/api/turnos", {"accion": "editar_fijo", "fijo_id": o["fijo_id"], "cliente": "Fijo Quién", "tel": "11", "nota": "", "quien": "Beto"})
    o2 = fila(nube, prox, "Fijo Quién")
    assert o2["creado_por"] == "Ana" and o2["modificado_por"] == "Beto" and o2["modificado_en"] >= o2["creado"]
    # al suspender un día, el turno de ese día desaparece; el resto de las semanas sigue
    nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": o2["id"], "quien": "Carla"})
    assert not [x for x in nube.turnos(prox) if x["cliente"] == "Fijo Quién"]
    assert fila(nube, (dt.date.fromisoformat(f) + dt.timedelta(days=21)).isoformat(), "Fijo Quién")["virtual"]


def test_lo_que_manda_la_pc_queda_sin_nombre_y_la_pc_no_borra_lo_anotado(nube):
    f = fecha(441)
    base = {"cancha_id": "f5b", "fecha": f, "ini": H(19), "fin": H(20), "cliente": "Del mostrador", "tel": "", "nota": "", "precio": 28000, "sena": 0,
            "estado": "reservado", "tipo_precio": "comun", "fijo_id": None, "ocurrencia": None, "bloqueado": 0}
    st, r = nube.api("POST", "/api/sync", {"turnos": [{**base, "id": "pcx-1"}]}, pc=True)          # la PC no manda creado_por ni modificado_por
    assert st == 200 and r["turnos"][0]["ok"]
    t = fila(nube, f, "Del mostrador")
    assert t["creado_por"] == "" and t["modificado_por"] == "" and t["modificado_en"] == 0 and t["creado"] > 0

    # la web edita el turno de la PC; después la PC lo cobra (sube sena y bloqueado, sin las columnas nuevas)
    nube.api("POST", "/api/turnos", {"accion": "editar", "id": "pcx-1", "cliente": "Del mostrador", "tel": "11 4444-5555", "nota": "", "quien": "Ana"})
    web = fila(nube, f, "Del mostrador")
    assert web["modificado_por"] == "Ana" and web["creado_por"] == ""
    st, r = nube.api("POST", "/api/sync", {"turnos": [{**base, "id": "pcx-1", "tel": "11 4444-5555", "sena": 5000, "bloqueado": 1, "base_mod": web["modificado"]}]}, pc=True)
    assert st == 200 and r["turnos"][0]["ok"]
    despues = fila(nube, f, "Del mostrador")
    assert despues["sena"] == 5000 and despues["bloqueado"] == 1
    assert despues["modificado_por"] == "Ana"                                  # no se pierde lo anotado
    assert despues["modificado"] > despues["modificado_en"]                     # y se nota que el último cambio vino de la PC
    # un turno bloqueado desde la PC no se toca desde la web
    st, r = nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": "pcx-1", "quien": "Ana"})
    assert st == 400 and "PC" in r["error"]


# ------------------------------------------------------------------ búsqueda

def buscar(nube, q):
    st, r = nube.api("GET", "/api/turnos/buscar?q=" + quote(q))
    assert st == 200
    return r


def nombres(r):
    return [t["cliente"] for t in r["turnos"]]


def test_buscar_sin_acentos_ni_mayusculas_y_con_pocas_letras(nube):
    nube.reservar("f5a", fecha(5), H(15), "Ñandú Fútbol Club", "11 3333-4444")
    nube.reservar("f5b", fecha(6), H(15), "José Núñez", "")
    nube.reservar("f5a", fecha(7), H(15), "Zulema Ríos", "011 15 5555-1234")
    for q in ["nandu", "ÑANDÚ", "Ñandú fútbol", "club nandu", "nan", "  nandu  "]:
        assert "Ñandú Fútbol Club" in nombres(buscar(nube, q)), q
    assert "José Núñez" in nombres(buscar(nube, "jose nunez"))
    assert "José Núñez" in nombres(buscar(nube, "NUÑ"))
    r = buscar(nube, "nandu")
    t = r["turnos"][0]
    assert t["cancha_id"] == "f5a" and t["fecha"] == fecha(5) and t["ini"] == H(15) and t["fin"] == H(16) and t["tel"] == "11 3333-4444" and t["id"]
    assert "creado_por" not in t                                                  # la búsqueda sólo devuelve lo necesario


def test_buscar_por_telefono_en_cualquier_formato(nube):
    nube.reservar("padelA", fecha(8), H(10), "Telefonista Uno", "011 15 5555-1234")
    nube.reservar("padelT", fecha(9), H(10), "Telefonista Dos", "+54 9 11 6000-1111")
    for q in ["5555-1234", "55551234", "1155551234", "(011) 15 5555 1234", "+54 9 11 5555 1234", "5555"]:
        assert "Telefonista Uno" in nombres(buscar(nube, q)), q
    for q in ["6000-1111", "1160001111", "011 6000 1111", "5491160001111"]:
        assert "Telefonista Dos" in nombres(buscar(nube, q)), q
    assert "Telefonista Uno" not in nombres(buscar(nube, "6000-1111"))
    assert buscar(nube, "99999999")["turnos"] == []


def test_buscar_tolera_un_error_de_tipeo_pero_prefiere_lo_exacto(nube):
    nube.reservar("f5a", fecha(10), H(16), "Marcelo Quiroga", "")
    assert "Marcelo Quiroga" in nombres(buscar(nube, "marselo"))                  # una letra cambiada
    assert "Marcelo Quiroga" in nombres(buscar(nube, "quiroha"))
    assert "Marcelo Quiroga" not in nombres(buscar(nube, "qwerty"))
    nube.reservar("f5b", fecha(10), H(16), "Marcela Quiroga", "")
    assert nombres(buscar(nube, "marcela")) == ["Marcela Quiroga"]                 # si hay coincidencia exacta no se mezclan parecidos


def test_buscar_ventana_de_fechas_orden_cancelados_y_limites(nube):
    nube.reservar("f5a", fecha(-40), H(15), "Zorro Muyviejo", "")
    nube.reservar("f5a", fecha(-20), H(15), "Zorro Viejo", "")
    nube.reservar("f5a", fecha(30), H(15), "Zorro Futuro", "")
    nube.reservar("f5a", fecha(2), H(15), "Zorro Cercano", "")
    nube.reservar("f5b", fecha(3), H(15), "Zorro Cancelado", "")
    nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": fila(nube, fecha(3), "Zorro Cancelado")["id"], "quien": "Ana"})
    r = buscar(nube, "zorro")
    assert nombres(r) == ["Zorro Viejo", "Zorro Cercano", "Zorro Futuro"]          # sin los de hace más de 30 días ni los cancelados, por fecha
    assert [t["fecha"] for t in r["turnos"]] == sorted(t["fecha"] for t in r["turnos"])
    assert r["hoy"] == fecha(0)
    # muy corta: no se consulta nada
    for q in ["", " ", "a", "1"]:
        r = buscar(nube, q)
        assert r["turnos"] == [] and r["fijos"] == [] and r.get("corta")
    # hay que estar adentro
    assert nube.api("GET", "/api/turnos/buscar?q=zorro", cookie="")[0] == 401


def test_buscar_tambien_entre_los_turnos_fijos_y_dice_cual_es_el_proximo(nube):
    d = dt.date.fromisoformat(fecha(1))
    nube.reservar("f5b", d.isoformat(), H(21), "Fijo Quimey", "11 7777-0000", fijo=True)
    r = buscar(nube, "quimey")
    assert r["turnos"] == [] and len(r["fijos"]) == 1                              # las ocurrencias no son turnos guardados: aparece el fijo
    f = r["fijos"][0]
    assert f["cancha_id"] == "f5b" and f["ini"] == H(21) and f["dow"] == (d.weekday() + 1) % 7 and f["proxima"] == d.isoformat() and f["tel"] == "11 7777-0000"
    # si se suspende esa fecha, el próximo es el de la semana siguiente
    nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": f"F:{f['id']}:{d.isoformat()}", "quien": "Ana"})
    assert buscar(nube, "quimey")["fijos"][0]["proxima"] == (d + dt.timedelta(days=7)).isoformat()
    # un fijo terminado ya no aparece
    nube.api("POST", "/api/turnos", {"accion": "terminar_fijo", "fijo_id": f["id"], "desde": fecha(0), "quien": "Ana"})
    assert buscar(nube, "quimey")["fijos"] == []


# ------------------------------------------------------------------ semana

def test_la_semana_trae_los_siete_dias_con_turnos_y_fijos(nube):
    lu = lunes(fecha(200))
    d = lambda n: (dt.date.fromisoformat(lu) + dt.timedelta(days=n)).isoformat()  # noqa: E731
    nube.reservar("f5a", d(0), H(20), "Lunes", "")
    nube.reservar("padelT", d(6), H(22), "Domingo", "")
    nube.reservar("f5b", d(7), H(20), "Semana siguiente", "")                      # fuera del rango
    nube.reservar("f5b", d(2), H(15), "Cancelado", "")
    nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": fila(nube, d(2), "Cancelado")["id"], "quien": "Ana"})
    nube.reservar("padelA", d(1), H(10), "Fijo Semanal", "11 1", fijo=True)         # martes: aparece cada martes desde ese
    nube.api("POST", "/api/turnos", {"accion": "cancelar", "id": f"F:{fila(nube, d(1), 'Fijo Semanal')['fijo_id']}:{d(8)}", "quien": "Ana"})
    st, r = nube.api("GET", f"/api/turnos/semana?desde={lu}")
    assert st == 200 and r["desde"] == lu and len(r["canchas"]) == 5
    assert sorted((t["fecha"], t["cliente"]) for t in r["turnos"]) == sorted([(d(0), "Lunes"), (d(6), "Domingo"), (d(1), "Fijo Semanal")])
    # coincide con lo que dice cada día por separado
    for n in range(7):
        por_dia = sorted(t["id"] for t in nube.turnos(d(n)))
        assert por_dia == sorted(t["id"] for t in r["turnos"] if t["fecha"] == d(n)), d(n)
    # la semana siguiente: el fijo suspendido ese martes no está; el del martes de más adelante sí
    r2 = nube.api("GET", f"/api/turnos/semana?desde={d(7)}")[1]
    assert sorted(t["cliente"] for t in r2["turnos"]) == ["Semana siguiente"]
    r3 = nube.api("GET", f"/api/turnos/semana?desde={d(14)}")[1]
    assert [t["cliente"] for t in r3["turnos"]] == ["Fijo Semanal"] and r3["turnos"][0]["fecha"] == d(15)
    assert nube.api("GET", "/api/turnos/semana?desde=ayer")[0] == 400


# ------------------------------------------------------------------ sincronización con la PC

def test_la_sincronizacion_de_la_pc_no_conoce_las_columnas_nuevas_y_sigue_andando(nube):
    f = fecha(450)
    st, r = nube.api("POST", "/api/sync", {"canchas": CANCHAS, "fijos": [{"id": "pcf-1", "cancha_id": "padelT", "dow": dt.date.fromisoformat(f).isoweekday() % 7, "ini": H(13), "cliente": "Fijo PC", "tel": "", "nota": "", "desde": f}],
                                          "turnos": [{"id": "pct-1", "cancha_id": "f5a", "fecha": f, "ini": H(18), "fin": H(19), "cliente": "Turno PC", "tel": "", "nota": "", "precio": 28000, "sena": 0, "estado": "reservado", "tipo_precio": "comun",
                                                      "fijo_id": None, "ocurrencia": None, "bloqueado": 0}]}, pc=True)
    assert st == 200 and r["canchas"] == 5 and r["fijos"][0]["ok"] and r["turnos"][0]["ok"]
    # lo que la PC baja trae las columnas nuevas (con '' donde no hay nombre) y sigue siendo lo mismo de siempre
    st, b = nube.api("GET", "/api/sync?desde=0", pc=True)
    assert st == 200
    t = next(x for x in b["turnos"] if x["id"] == "pct-1")
    assert t["creado_por"] == "" and t["modificado_por"] == "" and {"id", "cancha_id", "fecha", "ini", "fin", "cliente", "precio", "sena", "estado", "bloqueado", "modificado"} <= set(t)
    assert next(x for x in b["fijos"] if x["id"] == "pcf-1")["creado_por"] == ""
    # el conflicto de versiones y los choques siguen funcionando
    st, r = nube.api("POST", "/api/sync", {"turnos": [{**t, "cliente": "Pisado", "base_mod": 1}]}, pc=True)
    assert r["turnos"][0]["conflicto"] == "version"
    st, r = nube.api("POST", "/api/sync", {"turnos": [{**t, "id": "pct-2", "cancha_id": "f7", "base_mod": None}]}, pc=True)
    assert r["turnos"][0]["conflicto"] == "choque"
    assert nube.api("POST", "/api/sync", {"turnos": []})[0] == 401 and nube.api("GET", "/api/sync?desde=0")[0] == 401      # sin la clave de la PC no se entra


def cancha_local(i, clave, nombre, dur=60, ap=840, ci=1380, deporte="futbol"):
    return op("canchas", i, {"id": i, "clave": clave, "nombre": nombre, "deporte": deporte, "dur": dur, "ap": ap, "ci": ci, "precio": 28000, "precioFijo": 24000, "lugarId": None})


def test_el_programa_de_la_pc_sincroniza_de_verdad_con_la_nube_local(nube, almacen, db, rutas):
    f = fecha(460)
    almacen.aplicar(base_inicial(), "Caja", 0)
    almacen.aplicar({"formas": {"canchas": "lista", "turnos": "lista", "fijos": "lista", "comandas": "mapa"},
                     "ops": [cancha_local("cn1", "f5a", "Fútbol 5 · Cancha 1"), cancha_local("cn2", "f5b", "Fútbol 5 · Cancha 2"),
                             op("turnos", "t1", {"id": "t1", "canchaId": "cn1", "fecha": f, "ini": H(20), "fin": H(21), "cliente": "Del mostrador", "tel": "", "nota": "", "precio": 28000, "estado": "reservado",
                                                 "pagos": [], "ventaId": None, "esFijo": False, "fijoId": None, "clienteId": None, "tipoPrecio": "comun"})]}, "Caja", None)
    app = SimpleNamespace(db=db, almacen=almacen, diario=SimpleNamespace(marcar=lambda d: None), rutas=rutas, ajustes={"nube_url": nube.url, "nube_token": CLAVE_PC}, modo="real", apagar=threading.Event())
    sub = SubidaNube(app)
    s = sub.turnos
    assert s.sincronizar(), s.error
    arriba = fila(nube, f, "Del mostrador")
    assert arriba["cancha_id"] == "f5a" and arriba["creado_por"] == ""             # lo de la PC llega sin nombre

    # un celular carga otro turno y le cambia el nombre al de la PC: todo baja al programa, con las columnas nuevas de por medio
    assert nube.reservar("f5b", f, H(20), "Del celu", "11 5555-0000", quien="Ana")[0] == 200
    nube.api("POST", "/api/turnos", {"accion": "editar", "id": arriba["id"], "cliente": "Cambiado en el celu", "tel": "11 1", "nota": "", "quien": "Beto"})
    assert s.sincronizar(), s.error
    assert s.problemas == []
    con = db.lectura()
    try:
        locales = {r[0]: json.loads(r[1]) for r in con.execute("SELECT id, data FROM docs WHERE coleccion = 'turnos'")}
    finally:
        con.close()
    assert locales["t1"]["cliente"] == "Cambiado en el celu"
    celu = next(t for t in locales.values() if t["cliente"] == "Del celu")
    assert celu["canchaId"] == "cn2" and celu["tel"] == "11 5555-0000" and celu["pagos"] == []

    # la PC cobra una seña: sube, la web lo marca como bloqueado y conserva quién lo cargó
    almacen.aplicar({"ops": [op("turnos", "t1", {**locales["t1"], "pagos": [{"id": "p", "monto": 5000, "nombre": "Efectivo", "metodoId": "mp1"}]})]}, "Caja", None)
    assert s.sincronizar(), s.error
    t = fila(nube, f, "Cambiado en el celu")
    assert t["sena"] == 5000 and t["bloqueado"] == 1 and t["modificado_por"] == "Beto" and t["modificado"] > t["modificado_en"]


# ------------------------------------------------------------------ base de producción (todavía sin las columnas nuevas)

ESQUEMA_VIEJO = """
CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dias (dia TEXT PRIMARY KEY, resumen TEXT NOT NULL, xlsx TEXT, subido INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS canchas (id TEXT PRIMARY KEY, clave TEXT NOT NULL, nombre TEXT NOT NULL, deporte TEXT NOT NULL,
  dur INTEGER NOT NULL, ap INTEGER NOT NULL, ci INTEGER NOT NULL, precio INTEGER NOT NULL, precio_fijo INTEGER NOT NULL, orden INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS fijos (id TEXT PRIMARY KEY, cancha_id TEXT NOT NULL, dow INTEGER NOT NULL, ini INTEGER NOT NULL,
  cliente TEXT NOT NULL DEFAULT '', tel TEXT NOT NULL DEFAULT '', nota TEXT NOT NULL DEFAULT '', desde TEXT NOT NULL, hasta TEXT, modificado INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS turnos (id TEXT PRIMARY KEY, cancha_id TEXT NOT NULL, fecha TEXT NOT NULL, ini INTEGER NOT NULL, fin INTEGER NOT NULL,
  cliente TEXT NOT NULL DEFAULT '', tel TEXT NOT NULL DEFAULT '', nota TEXT NOT NULL DEFAULT '', precio INTEGER NOT NULL, sena INTEGER NOT NULL DEFAULT 0,
  estado TEXT NOT NULL DEFAULT 'reservado', tipo_precio TEXT NOT NULL DEFAULT 'comun', fijo_id TEXT, ocurrencia TEXT, creado INTEGER NOT NULL, modificado INTEGER NOT NULL,
  bloqueado INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS turnos_fecha ON turnos (fecha, estado);
CREATE TABLE IF NOT EXISTS registro_turnos (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, quien TEXT NOT NULL, accion TEXT NOT NULL, detalle TEXT NOT NULL);
INSERT OR IGNORE INTO canchas VALUES ('f5a','f5a','Fútbol 5 · Cancha 1','futbol',60,600,1440,28000,24000,1), ('f5b','f5b','Fútbol 5 · Cancha 2','futbol',60,600,1440,28000,24000,2),
 ('f7','f7','Fútbol 7 · Cancha completa','futbol',60,600,1440,48000,42000,3), ('padelT','padelT','Pádel · Techada','padel',90,480,1440,26000,22000,4),
 ('padelA','padelA','Pádel · Abierta','padel',90,480,1440,22000,19000,5);
INSERT INTO turnos VALUES ('viejo1','f5a','2031-05-05',1200,1260,'Turno de antes','','',28000,0,'reservado','comun',NULL,NULL,1000,1000,0);
INSERT INTO fijos VALUES ('fviejo','padelT',2,780,'Fijo de antes','','','2031-01-01',NULL,1000);
"""


def columnas(nube, tabla):
    return {r["name"]: (r["type"], r["notnull"], r["dflt_value"]) for r in nube.consulta(f"PRAGMA table_info({tabla})")}


def test_migraciones_sql_dejan_la_base_vieja_igual_que_el_esquema_nuevo(tmp_path):
    vieja = NubeLocal(tmp_path / "vieja", schema=ESQUEMA_VIEJO).preparar()
    nueva = NubeLocal(tmp_path / "nueva").preparar()
    vieja.sql((RAIZ / "nube" / "migraciones.sql").read_text(encoding="utf-8"))
    for tabla in ("turnos", "fijos"):
        assert columnas(vieja, tabla) == columnas(nueva, tabla), tabla
    # lo que ya había se conserva, con '' donde no hay nombre
    t = vieja.consulta("SELECT * FROM turnos WHERE id = 'viejo1'")[0]
    assert t["cliente"] == "Turno de antes" and t["creado_por"] == "" and t["modificado_por"] == "" and t["modificado_en"] == 0
    assert vieja.consulta("SELECT creado, creado_por FROM fijos")[0] == {"creado": 0, "creado_por": ""}


def test_si_se_publica_antes_de_migrar_la_web_agrega_las_columnas_sola(tmp_path):
    n = NubeLocal(tmp_path / "vieja", schema=ESQUEMA_VIEJO).iniciar()
    try:
        assert "creado_por" not in columnas(n, "turnos")
        # la PC sigue sincronizando como siempre aunque la base sea la vieja
        assert n.api("POST", "/api/sync", {"canchas": CANCHAS}, pc=True)[0] == 200
        assert n.api("GET", "/api/sync?desde=0", pc=True)[0] == 200
        f = "2031-05-05"
        st, r = n.reservar("f5b", f, H(17), "Primera desde el celu", quien="Ana")
        assert st == 200, r
        assert set(columnas(n, "turnos")) >= {"creado_por", "modificado_por", "modificado_en"} and "creado_por" in columnas(n, "fijos")
        ts = {t["cliente"]: t for t in n.turnos(f)}
        assert ts["Primera desde el celu"]["creado_por"] == "Ana" and ts["Turno de antes"]["creado_por"] == ""
        assert n.reservar("padelT", "2031-05-06", H(10), "Fijo nuevo", fijo=True, quien="Beto")[0] == 200
        assert n.api("GET", "/api/turnos/buscar?q=antes")[1]["fijos"][0]["cliente"] == "Fijo de antes"
    finally:
        n.parar()
