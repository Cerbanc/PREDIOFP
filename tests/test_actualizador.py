import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import base_inicial
from predio import VERSION, actualizador
from predio import app as app_mod
from predio.config import AJUSTES_POR_DEFECTO
from predio.servidor import Servidor

EXE = b"MZ" + b"\x00" * 5000


@pytest.fixture()
def github(monkeypatch):
    estado = {"version": "9.9.9", "sha": hashlib.sha256(EXE).hexdigest(), "tam": len(EXE), "contenido": EXE}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/repos/club/predio/releases/latest":
                cuerpo = json.dumps({"tag_name": "v" + estado["version"], "body": "Nuevo: lápiz de precio", "published_at": "2026-10-10T10:00:00Z", "assets": [
                    {"name": "Caja-del-Predio-portable.zip", "browser_download_url": f"http://127.0.0.1:{self.server.server_port}/dl/otro.zip", "size": 1},
                    {"name": f"Instalar-Caja-del-Predio-{estado['version']}.exe", "size": estado["tam"], "digest": "sha256:" + estado["sha"],
                     "browser_download_url": f"http://127.0.0.1:{self.server.server_port}/dl/Instalar-Caja-del-Predio-{estado['version']}.exe"}]}).encode()
            elif self.path.startswith("/dl/Instalar-"):
                cuerpo = estado["contenido"]
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Length", str(len(cuerpo))); self.end_headers(); self.wfile.write(cuerpo)

    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    monkeypatch.setenv("PREDIO_API_GITHUB", f"http://127.0.0.1:{s.server_port}")
    yield estado
    s.shutdown()


def test_versiones_se_comparan_como_numeros():
    assert actualizador.version_tupla("v1.10.0") > actualizador.version_tupla("1.9.9")
    assert actualizador.version_tupla("1.0.0") == actualizador.version_tupla("v1.0.0")


def test_busca_y_detecta_la_version_nueva(github):
    r = actualizador.buscar("club/predio")
    assert r["hay"] is True and r["version"] == "9.9.9" and r["actual"] == VERSION and "lápiz" in r["notas"] and r["url"].endswith(".exe")
    github["version"] = VERSION
    assert actualizador.buscar("club/predio")["hay"] is False


def test_errores_de_consulta_son_claros(github):
    assert "privado" in actualizador.buscar("club/otro")["error"]
    assert "mal escrito" in actualizador.buscar("../x")["error"]


def test_descarga_verifica_tamano_y_huella(github, tmp_path):
    info = actualizador.buscar("club/predio")
    ruta = actualizador.descargar(info, tmp_path)
    assert ruta.read_bytes() == EXE and not list(tmp_path.glob("*.parte"))
    for malo in ({**info, "sha256": "0" * 64}, {**info, "bytes": 5}):
        with pytest.raises(actualizador.ErrorActualizacion):
            actualizador.descargar(malo, tmp_path / "otra")
    assert not list((tmp_path / "otra").glob("*.exe")) and not list((tmp_path / "otra").glob("*.parte"))


def test_no_descarga_de_hosts_ajenos(tmp_path):
    for url in ("https://evil.example.com/Instalar-Caja-del-Predio-9.exe", "file:///C:/x/Instalar-Caja-del-Predio-9.exe"):
        with pytest.raises(actualizador.ErrorActualizacion):
            actualizador.descargar({"url": url}, tmp_path)
    with pytest.raises(actualizador.ErrorActualizacion):
        actualizador.descargar({"url": "https://github.com/x/y/releases/download/v1/otro.exe"}, tmp_path)


def test_api_actualiza_hace_copia_y_pide_cerrar(github, rutas, monkeypatch):
    import urllib.request

    app = app_mod.crear_aplicacion(rutas, dict(AJUSTES_POR_DEFECTO, actualizaciones_repo="club/predio"), "real")
    s = Servidor(app, 0); s.iniciar()
    try:
        def pedir(m, ruta, cuerpo=None):
            req = urllib.request.Request(f"http://127.0.0.1:{s.puerto}{ruta}", data=cuerpo, method=m, headers={"X-Predio-Token": app.token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        app.almacen.aplicar({**base_inicial()}, "Sistema", 0)
        estado, r = pedir("GET", "/api/actualizacion")
        assert estado == 200 and r["hay"] and r["version"] == "9.9.9"
        estado, r = pedir("POST", "/api/actualizar", b"{}")
        assert estado == 200 and app.apagar.is_set() and app.instalador_pendiente.read_bytes() == EXE
        assert any("antes_de_actualizar" in c["archivo"] for c in app.copias.listar())
        github["version"] = VERSION
        app.apagar.clear(); app._actualizacion = None
        estado, r = pedir("POST", "/api/actualizar", b"{}")
        assert estado == 400 and not app.apagar.is_set()
    finally:
        s.parar(); app_mod.cerrar_aplicacion(app)
