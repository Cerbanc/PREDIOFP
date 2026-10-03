"""Prueba de punta a punta: programa real + navegador real. Se salta sola si no hay node/playwright."""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
PLAYWRIGHT = os.environ.get("PLAYWRIGHT_PATH") or "/opt/node22/lib/node_modules/playwright"
pytestmark = pytest.mark.skipif(not shutil.which("node") or not Path(PLAYWRIGHT).exists(), reason="falta node o playwright")


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def arrancar(datos, puerto):
    p = subprocess.Popen([sys.executable, "-m", "predio", "--datos", str(datos), "--sin-ventana", "--puerto", str(puerto)], cwd=RAIZ,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=dict(os.environ, PREDIO_ESPERA_INICIO="0.5"))
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/ping", timeout=0.5).read()
            return p
        except Exception:
            if p.poll() is not None:
                raise RuntimeError("el programa se cerró: " + p.stdout.read())
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("el programa no arrancó")


def parar(p):
    p.terminate()
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()
        raise


def correr_node(fase, puerto, fotos=""):
    env = dict(os.environ, PORT=str(puerto), FASE=fase, FOTOS=fotos, PLAYWRIGHT_PATH=PLAYWRIGHT)
    r = subprocess.run(["node", str(RAIZ / "tests" / "e2e" / "e2e.js")], env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr


def test_punta_a_punta(tmp_path):
    datos = tmp_path / "Predio"
    puerto = puerto_libre()
    fotos = os.environ.get("E2E_FOTOS", "")
    p = arrancar(datos, puerto)
    try:
        correr_node("1", puerto, fotos)
    finally:
        parar(p)
    # el programa cerró bien: base consistente, copia de cierre hecha, carpeta del día con su Excel y su registro
    con = sqlite3.connect(datos / "datos" / "predio.db")
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT COUNT(*) FROM v_productos").fetchone()[0] == 6
    assert con.execute("SELECT COUNT(*) FROM v_ventas").fetchone()[0] == 1
    assert con.execute("SELECT SUM(total) FROM v_ventas").fetchone()[0] == 7900
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE importante = 1").fetchone()[0] > 3
    con.close()
    copias = list((datos / "copias").glob("predio_*.db"))
    assert any("_cierre" in c.name for c in copias) and any("_inicio" in c.name for c in copias)
    dias = [d for d in (datos / "dias").iterdir() if d.is_dir()]
    assert len(dias) == 1
    assert (dias[0] / f"{dias[0].name}_resumen.xlsx").exists() and (dias[0] / f"{dias[0].name}_registro.txt").exists()
    registro = (dias[0] / f"{dias[0].name}_registro.txt").read_text(encoding="utf-8")
    assert "Ticket #1 cobrado" in registro and "Marcos" in registro
    assert (datos / "LEEME.txt").exists() and (datos / "configuracion.json").exists()
    # se vuelve a abrir el programa: todo sigue ahí
    p = arrancar(datos, puerto)
    try:
        correr_node("2", puerto, fotos)
    finally:
        parar(p)
