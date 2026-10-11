"""Rellenado de la nube: completar con los días de atrás que la nube no tiene o tiene distintos (predio/nube.py)."""
import datetime as dt
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import base_inicial, op
from predio.nube import SubidaNube, armar_pedido, huella_del_dia
from predio.util import ahora_ms
from test_diario_copias import venta


class NubeFalsa:
    """Imita la parte de la nube que usa el relleno: GET /api/subidos (día -> huella) y POST /api/subir. Guarda lo que le suben."""

    def __init__(self):
        self.dias: dict[str, dict] = {}          # dia -> resumen
        self.pedidos: list[tuple[str, str]] = []  # (metodo, ruta)
        self.fallas: list[tuple[str, int]] = []   # (método, código) a devolver, en orden, antes de responder bien
        self.sin_endpoint = False                 # nube vieja: no conoce /api/subidos

        nube = self

        class H(BaseHTTPRequestHandler):
            def _responder(self, codigo, cuerpo):
                datos = json.dumps(cuerpo).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

            def _falla(self, metodo):
                for i, (m, c) in enumerate(nube.fallas):
                    if m == metodo:
                        nube.fallas.pop(i)
                        return c
                return None

            def do_GET(self):
                u = urlparse(self.path)
                nube.pedidos.append(("GET", u.path))
                if self.headers.get("Authorization") != "Bearer secreto":
                    return self._responder(401, {"error": "clave incorrecta"})
                if nube.sin_endpoint:
                    return self._responder(404, {"error": "no existe"})
                c = self._falla("GET")
                if c:
                    return self._responder(c, {"error": "falló"})
                desde = parse_qs(u.query)["desde"][0]
                self._responder(200, {"dias": {d: r.get("huella", "") for d, r in nube.dias.items() if d >= desde}})

            def do_POST(self):
                cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                nube.pedidos.append(("POST", self.path))
                c = self._falla("POST")
                if c:
                    return self._responder(c, {"error": "falló"})
                nube.dias[cuerpo["dia"]] = cuerpo["resumen"]
                self._responder(200, {"ok": True})

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.srv.server_port}"

    def subidos(self):
        return [p for p in self.pedidos if p[0] == "POST"]


@pytest.fixture()
def nube():
    n = NubeFalsa()
    yield n
    n.srv.shutdown()


def ts_de(dias_atras: int, hora: int = 12) -> int:
    d = dt.date.today() - dt.timedelta(days=dias_atras)
    return int(dt.datetime.combine(d, dt.time(hora, 0)).timestamp() * 1000)


def dia_atras(n: int) -> str:
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


def con_ventas(almacen, db, nube, atras, **ajustes):
    """Un programa con ventas en los días (hace N días) indicados y una nube falsa."""
    almacen.aplicar(base_inicial(), "Caja", 0)
    almacen.aplicar({"ops": [op("ventas", f"v{n}", venta(f"v{n}", 100 + n, ts_de(n), 4400 + n)) for n in atras]}, "Caja", None)
    app = SimpleNamespace(db=db, ajustes={"nube_url": nube.url, "nube_token": "secreto", **ajustes}, modo="real", apagar=threading.Event())
    s = SubidaNube(app)
    s.RELLENO_PAUSA = 0.0
    return s


def test_la_huella_ignora_la_hora_y_el_stock_pero_no_las_ventas(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1])
    con = db.lectura()
    try:
        a = armar_pedido(con, dia_atras(1))["resumen"]
    finally:
        con.close()
    b = dict(a, generado=a["generado"] + 5000, stock_bajo=[{"producto": "X", "stock": 0, "minimo": 5}])
    assert huella_del_dia(a) == huella_del_dia(b) == a["huella"]
    c = json.loads(json.dumps(a))
    c["kpis"]["total_vendido"] += 1
    assert huella_del_dia(c) != a["huella"]


def test_sube_los_dias_que_faltan_del_mas_nuevo_al_mas_viejo_y_de_a_pocos(almacen, db, nube):
    # hubo ventas hace 1, 2, 3, 5 y 10 días: desde hace 10 días hasta ayer son 10 días, con huecos que deben quedar como "sin movimiento"
    s = con_ventas(almacen, db, nube, [1, 2, 3, 5, 10])
    assert s.rellenar(maximo=4) == 4
    assert s.relleno["quedan"] is True and s.relleno["error"] is None
    assert set(nube.dias) == {dia_atras(n) for n in (1, 2, 3, 4)}          # primero lo más nuevo
    assert s.rellenar(maximo=4) == 4 and s.relleno["quedan"] is True
    assert s.rellenar(maximo=4) == 2 and s.relleno["quedan"] is False
    assert set(nube.dias) == {dia_atras(n) for n in range(1, 11)}          # ni hoy ni antes del primer día con ventas
    vacio = nube.dias[dia_atras(4)]["kpis"]
    assert vacio["tickets"] == 0 and vacio["total_vendido"] == 0 and nube.dias[dia_atras(5)]["kpis"]["tickets"] == 1
    n = len(nube.subidos())
    assert s.rellenar() == 0 and len(nube.subidos()) == n                  # ya está todo igual: no sube nada


def test_si_un_dia_viejo_cambia_solo_se_vuelve_a_subir_ese(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1, 2, 3])
    s.rellenar()
    n = len(nube.subidos())
    almacen.aplicar({"ops": [op("ventas", "v9", venta("v9", 9, ts_de(2, 15), 999))]}, "Caja", None)      # se corrigió el día de anteayer
    assert s.rellenar() == 1 and len(nube.subidos()) == n + 1
    assert nube.dias[dia_atras(2)]["kpis"]["total_vendido"] == 4402 + 999
    # una huella distinta o ausente en la nube (por ejemplo, un día subido por una versión vieja) también se vuelve a subir
    nube.dias[dia_atras(3)].pop("huella")
    assert s.rellenar() == 1


def test_el_stock_actual_no_hace_volver_a_subir_dias_viejos(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1, 2, 3])
    s.rellenar()
    n = len(nube.subidos())
    almacen.aplicar({"ops": [op("productos", "p1", {"id": "p1", "nombre": "Coca-Cola 500 ml", "catId": "c1", "tipo": "directo", "precio": 2200, "costo": 1400, "stock": 0, "minimo": 6,
                                                     "activo": True, "paqueteUnidades": 1, "receta": []})]}, "Caja", None)
    assert s.rellenar() == 0 and len(nube.subidos()) == n


def test_no_toca_hoy_ni_el_aviso_de_que_la_pc_esta_viva(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [0, 1, 2])
    s.ultima_subida = None
    assert s.rellenar() == 2
    assert dt.date.today().isoformat() not in nube.dias
    assert s.ultima_subida is None and s.ultimo_error is None            # eso es del ciclo normal; el relleno no lo pisa


def test_respeta_el_tope_de_dias_y_se_puede_apagar(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1, 20], nube_relleno_dias=5)
    assert s.rellenar() == 5 and min(nube.dias) == dia_atras(5)
    s.app.ajustes["nube_relleno_dias"] = 0
    pedidos = len(nube.pedidos)
    assert s.rellenar() == 0 and len(nube.pedidos) == pedidos             # 0 = no completar: ni siquiera pregunta


def test_sin_ventas_no_hace_nada(almacen, db, nube):
    almacen.aplicar(base_inicial(), "Caja", 0)
    app = SimpleNamespace(db=db, ajustes={"nube_url": nube.url, "nube_token": "secreto"}, modo="real", apagar=threading.Event())
    s = SubidaNube(app)
    assert s.rellenar() == 0 and nube.pedidos == [] and s.relleno["error"] is None


def test_si_la_nube_falla_no_rompe_y_reintenta(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1, 2, 3])
    nube.fallas = [("GET", 500)]
    assert s.rellenar() == 0 and "500" in s.relleno["error"] and nube.dias == {}
    nube.fallas = [("POST", 503)]                                          # falla en medio de la subida
    assert s.rellenar() == 0 and "503" in s.relleno["error"]
    assert s.rellenar() == 3 and s.relleno["error"] is None


def test_nube_vieja_sin_la_lista_de_dias_no_rompe(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1, 2])
    nube.sin_endpoint = True
    assert s.rellenar() == 0 and "404" in s.relleno["error"] and nube.subidos() == []


def test_sin_internet_no_rompe(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1, 2])
    nube.srv.shutdown()
    nube.srv.server_close()
    assert s.rellenar() == 0 and s.relleno["error"]


def test_clave_incorrecta_queda_anotada(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1, 2])
    s.app.ajustes["nube_token"] = "otra"
    assert s.rellenar() == 0 and "401" in s.relleno["error"]


def test_el_hilo_completa_solo_y_se_apaga_con_el_programa(almacen, db, nube):
    s = con_ventas(almacen, db, nube, [1, 2, 3, 4, 5])
    s.RELLENO_ESPERA_INICIAL = 0.05
    s.RELLENO_POR_VUELTA = 2
    s.RELLENO_SI_QUEDAN = 0.05
    s.RELLENO_CADA = 0.2
    s.iniciar()
    try:
        for _ in range(100):
            if len(nube.dias) >= 5:
                break
            time.sleep(0.05)
        assert set(nube.dias) == {dia_atras(n) for n in range(1, 6)}
        assert s._hilo_relleno.is_alive()
    finally:
        s.app.apagar.set()
    s._hilo_relleno.join(timeout=5)
    assert not s._hilo_relleno.is_alive()
    assert s.estado()["relleno"]["error"] is None
