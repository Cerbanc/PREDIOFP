import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

from conftest import base_inicial, op
from predio import nube
from predio.nube import SubidaNube, armar_pedido
from predio.util import ahora_ms, dia_de
from test_diario_copias import venta


def servidor_falso(respuestas):
    recibidos = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            cuerpo = self.rfile.read(int(self.headers["Content-Length"]))
            recibidos.append((self.path, self.headers.get("Authorization"), json.loads(cuerpo)))
            codigo = respuestas.pop(0) if respuestas else 200
            self.send_response(codigo); self.end_headers(); self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, recibidos


def preparar(almacen, db):
    almacen.aplicar(base_inicial(), "Caja", 0)
    ts = ahora_ms()
    almacen.aplicar({"ops": [op("cajas", "cx1", {"id": "cx1", "abierta": ts - 3_600_000, "fondo": 100, "responsable": "Ana", "cerrada": None}),
                             op("ventas", "v1", venta("v1", 1, ts - 60_000, 4400))]}, "Caja", None)
    return SimpleNamespace(db=db, ajustes={}, modo="real", apagar=threading.Event())


def test_el_pedido_trae_resumen_y_excel(almacen, db):
    app = preparar(almacen, db)
    con = db.lectura()
    try:
        p = armar_pedido(con, dia_de(ahora_ms()))
    finally:
        con.close()
    assert p["resumen"]["kpis"]["total_vendido"] == 4400 and p["resumen"]["cajas"][0]["responsable"] == "Ana"
    assert "telefono" not in json.dumps(p["resumen"]).lower() and "tel" not in p["resumen"]
    import base64
    assert base64.b64decode(p["xlsx"])[:2] == b"PK"


def test_sube_con_clave_y_no_repite_si_no_hay_novedades(almacen, db):
    app = preparar(almacen, db)
    srv, recibidos = servidor_falso([])
    app.ajustes = {"nube_url": f"http://127.0.0.1:{srv.server_port}", "nube_token": "secreto"}
    s = SubidaNube(app)
    assert s.activa and s.subir_ahora() is True
    assert recibidos and recibidos[0][0] == "/api/subir" and recibidos[0][1] == "Bearer secreto"
    n = len(recibidos)
    assert s.subir_ahora() is True and len(recibidos) == n          # sin cambios: no vuelve a subir
    srv.shutdown()


def test_si_la_nube_falla_no_rompe_y_reintenta(almacen, db):
    app = preparar(almacen, db)
    srv, recibidos = servidor_falso([500])
    app.ajustes = {"nube_url": f"http://127.0.0.1:{srv.server_port}", "nube_token": "x"}
    s = SubidaNube(app)
    assert s.subir_ahora() is False and s.ultimo_error
    assert s.subir_ahora() is True
    srv.shutdown()


def test_sin_configurar_no_hace_nada(almacen, db):
    app = preparar(almacen, db)
    assert not SubidaNube(app).activa
    app.ajustes = {"nube_url": "http://ejemplo.com", "nube_token": "x"}      # sin https no se sube
    assert not SubidaNube(app).activa
