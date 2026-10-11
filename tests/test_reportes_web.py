"""La web de reportes por período contra una nube local real (Worker + base D1 simulados con wrangler), con navegador real.
Levanta y apaga su propia nube en un puerto libre. Se salta si falta node, playwright o wrangler (/tmp/w/node_modules/.bin/wrangler, o WRANGLER_PATH)."""
import datetime as dt
import hashlib
import http.client
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import reportes_datos as rd
from conftest import base_inicial, op
from predio.nube import SubidaNube
from test_diario_copias import venta

RAIZ = Path(__file__).resolve().parents[1]
PLAYWRIGHT = os.environ.get("PLAYWRIGHT_PATH") or "/opt/node22/lib/node_modules/playwright"
WRANGLER = os.environ.get("WRANGLER_PATH") or "/tmp/w/node_modules/.bin/wrangler"
pytestmark = pytest.mark.skipif(not shutil.which("node") or not Path(PLAYWRIGHT).exists() or not Path(WRANGLER).exists(), reason="falta node, playwright o wrangler")

CLAVE_PC, CLAVE_VER = "tokenpc", "clave123"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def hoy_argentina():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).date()


def larga(d):
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def redondeo_js(x):
    return math.floor(x + 0.5)


@pytest.fixture(scope="module")
def nube(tmp_path_factory):
    """Una nube local vacía (esquema real, claves de prueba) en un puerto libre."""
    carpeta = tmp_path_factory.mktemp("nube")
    puerto = puerto_libre()
    env = dict(os.environ, CI="1", WRANGLER_SEND_METRICS="false")
    for sql in (["--file", "nube/schema.sql"], ["--command", f"INSERT OR REPLACE INTO config VALUES ('hash_pc','{sha(CLAVE_PC)}'),('hash_ver','{sha(CLAVE_VER)}')"]):
        r = subprocess.run([WRANGLER, "d1", "execute", "DB", "--local", "--persist-to", str(carpeta), *sql], cwd=RAIZ, env=env, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
    log = open(carpeta / "wrangler.log", "w")
    p = subprocess.Popen([WRANGLER, "dev", "--local", "--port", str(puerto), "--persist-to", str(carpeta)], cwd=RAIZ, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    url = f"http://127.0.0.1:{puerto}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "/", timeout=1).read()
                break
            except (urllib.error.URLError, OSError):
                if p.poll() is not None:
                    raise RuntimeError("wrangler se cerró: " + (carpeta / "wrangler.log").read_text())
                time.sleep(1)
        else:
            raise RuntimeError("wrangler no arrancó: " + (carpeta / "wrangler.log").read_text())
        yield url
    finally:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        log.close()


@pytest.fixture(scope="module")
def datos(nube):
    """100 días de datos hasta hoy. La semana pasada: lunes sin movimiento, martes y miércoles que la PC no subió."""
    hoy = hoy_argentina()
    lunes = hoy - dt.timedelta(days=hoy.weekday() + 7)
    faltan = tuple((lunes + dt.timedelta(days=i)).isoformat() for i in (1, 2))
    cerrados = (lunes.isoformat(),)
    rd.sembrar(nube, CLAVE_PC, hoy, 100, faltan, cerrados)
    return {"hoy": hoy, "lunes": lunes, "faltan": faltan, "cerrados": cerrados}


def resumen(d, datos):
    return rd.resumen_dia(d.isoformat(), escala=0 if d.isoformat() in datos["cerrados"] else 1.0)


def esperado_periodo(desde, hasta, datos):
    """Lo que tendría que mostrar la web, calculado a mano con el mismo generador de datos."""
    t = {"total": 0, "tickets": 0, "dias": 0, "faltan": 0, "cajas": 0, "cajas_dif": 0, "dias_dif": set(), "dias_cierre": set(), "por_dia": {}}
    d = desde
    while d <= hasta:
        if d.isoformat() in datos["faltan"]:
            t["faltan"] += 1
        else:
            r = resumen(d, datos)
            t["total"] += r["kpis"]["total_vendido"]
            t["tickets"] += r["kpis"]["tickets"]
            t["por_dia"][d.isoformat()] = r["kpis"]["total_vendido"]
            for c in r["cajas"]:
                t["cajas"] += 1
                t["dias_cierre"].add(d)
                if c["diferencia"] or any(g["diferencia"] for g in c["digital"]):
                    t["dias_dif"].add(d)
                    t["cajas_dif"] += 1
        t["dias"] += 1
        d += dt.timedelta(days=1)
    return t


def sesion(url):
    """Cookie de sesión de la web (login por formulario, sin seguir la redirección)."""
    h = http.client.HTTPConnection(url.split("//")[1])
    h.request("POST", "/entrar", body="clave=" + CLAVE_VER, headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = h.getresponse()
    r.read()
    return r.getheader("Set-Cookie").split(";")[0]


def pedir(url, ruta, cookie=None, token=None):
    req = urllib.request.Request(url + ruta, headers={k: v for k, v in (("Cookie", cookie), ("Authorization", f"Bearer {token}" if token else None)) if v})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_api_rango_calcula_el_periodo_y_el_anterior(nube, datos):
    hoy, lunes = datos["hoy"], datos["lunes"]
    dom = lunes + dt.timedelta(days=6)
    ant = lunes - dt.timedelta(days=7)
    cookie = sesion(nube)
    estado, r = pedir(nube, f"/api/rango?desde={lunes}&hasta={dom}&ant_desde={ant}&ant_hasta={ant + dt.timedelta(days=6)}", cookie)
    assert estado == 200
    e, ea = esperado_periodo(lunes, dom, datos), esperado_periodo(ant, ant + dt.timedelta(days=6), datos)
    assert [d["estado"] for d in r["dias"]] == ["vacio", "falta", "falta", "datos", "datos", "datos", "datos"]
    assert r["totales"]["total_vendido"] == e["total"] and r["totales"]["tickets"] == e["tickets"]
    assert r["totales"]["dias_sin_datos"] == 2 and r["totales"]["dias_vacios"] == 1 and r["totales"]["dias_con_datos"] == 4
    assert r["anterior"]["totales"]["total_vendido"] == ea["total"] and r["anterior"]["totales"]["dias_con_datos"] == 7
    assert r["cierres_resumen"]["cajas"] == e["cajas"] and r["cierres_resumen"]["dias_con_diferencia"] == len(e["dias_dif"])
    assert "cambios_importantes" not in json.dumps(r)                       # lo pesado no viaja
    # futuro: los días que todavía no llegaron no cuentan como que faltan
    estado, r = pedir(nube, f"/api/rango?desde={hoy}&hasta={hoy + dt.timedelta(days=3)}", cookie)
    assert [d["estado"] for d in r["dias"]] == ["datos", "futuro", "futuro", "futuro"] and r["totales"]["dias_sin_datos"] == 0 and r["totales"]["dias_total"] == 1


def test_api_rango_valida_y_pide_sesion(nube, datos):
    cookie = sesion(nube)
    assert pedir(nube, "/api/rango?desde=2026-01-01&hasta=2026-01-02")[0] == 401                       # sin sesión
    assert pedir(nube, "/api/rango?desde=2026-01-01&hasta=2026-01-02", token=CLAVE_PC)[0] == 401        # la clave de la PC no sirve para mirar
    for q, texto in (("desde=2026-02-31&hasta=2026-03-02", "no son válidas"), ("desde=2026-03-09&hasta=2026-03-01", "anterior"), ("desde=2025-01-01&hasta=2026-12-31", "máximo"),
                     ("desde=2026-03-01&hasta=2026-03-02&ant_desde=2026-02-01", "no son válidas"), ("hasta=2026-03-02", "no son válidas")):
        estado, r = pedir(nube, "/api/rango?" + q, cookie)
        assert estado == 400 and texto in r["error"], (q, r)
    # un año entero de una sola vez entra (el tope es de 400 días)
    hoy = datos["hoy"]
    estado, r = pedir(nube, f"/api/rango?desde={hoy - dt.timedelta(days=364)}&hasta={hoy}", cookie)
    assert estado == 200 and len(r["dias"]) == 365 and r["totales"]["dias_sin_datos"] > 200


def test_api_subidos_es_solo_para_la_pc(nube, datos):
    assert pedir(nube, "/api/subidos?desde=2026-01-01")[0] == 401
    assert pedir(nube, "/api/subidos?desde=2026-01-01", cookie=sesion(nube))[0] == 401                 # la sesión de la web no sirve
    assert pedir(nube, "/api/subidos?desde=basura", token=CLAVE_PC)[0] == 400
    desde = datos["hoy"] - dt.timedelta(days=3)
    estado, r = pedir(nube, f"/api/subidos?desde={desde}", token=CLAVE_PC)
    assert estado == 200 and set(r["dias"]) == {(datos["hoy"] - dt.timedelta(days=i)).isoformat() for i in range(4)}
    assert r["dias"][datos["hoy"].isoformat()] == f"h-{datos['hoy']}-0"


def test_la_pagina_de_salir_no_se_rompe(nube):
    h = http.client.HTTPConnection(nube.split("//")[1])
    h.request("GET", "/salir")
    r = h.getresponse()
    r.read()
    assert r.status == 303 and r.getheader("Location") == "/"


def test_pagina_con_navegador(nube, datos, tmp_path):
    hoy, lunes = datos["hoy"], datos["lunes"]
    dom = lunes + dt.timedelta(days=6)
    e = esperado_periodo(lunes, dom, datos)
    ant = esperado_periodo(lunes - dt.timedelta(days=7), lunes - dt.timedelta(days=1), datos)
    pct = redondeo_js((e["total"] - ant["total"]) / ant["total"] * 100)
    primero_mes = hoy.replace(day=1)
    mes_pasado = (primero_mes - dt.timedelta(days=1)).replace(day=1)
    fin_mes = primero_mes - dt.timedelta(days=1)
    m = esperado_periodo(mes_pasado, fin_mes, datos)
    jue, vie, sab = (lunes + dt.timedelta(days=i) for i in (3, 4, 5))
    cierres_dias = len(e["dias_cierre"])
    esperado = {
        "hoy": hoy.isoformat(), "futuro": (hoy + dt.timedelta(days=21)).isoformat(), "hoy_menos_89": (hoy - dt.timedelta(days=89)).isoformat(), "hoy_menos_450": (hoy - dt.timedelta(days=450)).isoformat(), "hoy_menos_6": (hoy - dt.timedelta(days=6)).isoformat(),
        "semana": {
            "lunes": lunes.isoformat(), "martes": (lunes + dt.timedelta(days=1)).isoformat(), "total": e["total"], "tickets": e["tickets"],
            "delta_total": ("+" if pct > 0 else "−") + str(abs(pct)) if pct else "",
            "miercoles_largo": larga(lunes + dt.timedelta(days=2)), "sabado_largo": larga(sab), "viernes": vie.isoformat(),
            "viernes_total": e["por_dia"][vie.isoformat()], "jueves_total": e["por_dia"][jue.isoformat()],
            "cierres": e["cajas"], "cierres_con_dif": e["cajas_dif"],
            "cierres_resumen": f"Hubo diferencia en {len(e['dias_dif'])} de {cierres_dias} días" if e["dias_dif"] else "Todas las cajas cuadraron",
        },
        "mes": {"ref": mes_pasado.isoformat(), "nombre": MESES[mes_pasado.month - 1], "total": m["total"], "dias": fin_mes.day, "faltan": m["faltan"]},
    }
    assert esperado["semana"]["delta_total"], "los datos de prueba no deberían dar exactamente 0% de cambio"
    fotos = os.environ.get("E2E_FOTOS", "")
    env = dict(os.environ, URL=nube, ESPERADO=json.dumps(esperado), FOTOS=fotos, PLAYWRIGHT_PATH=PLAYWRIGHT)
    r = subprocess.run(["node", str(RAIZ / "tests" / "e2e" / "reportes_web.js")], env=env, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stdout + r.stderr


def test_el_relleno_del_programa_funciona_contra_la_nube_real(nube, almacen, db):
    """Va último: pisa unos días de los datos de prueba. El programa completa en la nube real los días que le faltan y no repite lo que ya está igual."""
    almacen.aplicar(base_inicial(), "Caja", 0)
    ts = lambda n: int(dt.datetime.combine(dt.date.today() - dt.timedelta(days=n), dt.time(12, 0)).timestamp() * 1000)      # noqa: E731
    almacen.aplicar({"ops": [op("ventas", f"v{n}", venta(f"v{n}", 50 + n, ts(n), 7000 + n)) for n in (1, 2, 3)]}, "Caja", None)
    app = type("A", (), {})()
    app.db, app.ajustes, app.modo, app.apagar = db, {"nube_url": nube, "nube_token": CLAVE_PC, "nube_relleno_dias": 3}, "real", threading.Event()
    s = SubidaNube(app)
    s.RELLENO_PAUSA = 0
    assert s.rellenar() == 3 and s.relleno["error"] is None
    assert s.rellenar() == 0                                                    # la nube real devuelve las mismas huellas que arma el programa
    hoy = dt.date.today()
    estado, r = pedir(nube, f"/api/subidos?desde={hoy - dt.timedelta(days=3)}", token=CLAVE_PC)
    assert estado == 200 and all(len(r["dias"][(hoy - dt.timedelta(days=n)).isoformat()]) == 24 for n in (1, 2, 3))
    estado, rango = pedir(nube, f"/api/rango?desde={hoy - dt.timedelta(days=3)}&hasta={hoy - dt.timedelta(days=1)}", sesion(nube))
    assert [d["total"] for d in rango["dias"]] == [7003, 7002, 7001]
