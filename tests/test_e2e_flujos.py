"""Tercera prueba con navegador: cuentas de mesa, anulación de ticket, foto de producto, copias y borrado del historial."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import base_inicial, op  # noqa: E402
from predio.almacen import Almacen  # noqa: E402
from predio.config import Rutas  # noqa: E402
from predio.db import BaseDeDatos  # noqa: E402
from predio.util import ahora_ms  # noqa: E402
from test_e2e import PLAYWRIGHT, RAIZ, arrancar, parar, puerto_libre  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("node") or not Path(PLAYWRIGHT).exists(), reason="falta node o playwright")


def test_flujos_de_cuentas_tickets_fotos_y_copias(tmp_path):
    datos = tmp_path / "Predio"
    rutas = Rutas(datos).crear()
    db = BaseDeDatos(rutas.base)
    a = Almacen(db, rutas)
    base = base_inicial()
    base["ops"].append(op("cajas", "cx1", {"id": "cx1", "abierta": ahora_ms() - 3_600_000, "fondo": 1000, "responsable": "Marcos", "cerrada": None}))
    a.aplicar(base, "Sistema", 0)
    db.cerrar()
    puerto = puerto_libre()
    p = arrancar(datos, puerto)
    try:
        env = dict(os.environ, PORT=str(puerto), PLAYWRIGHT_PATH=PLAYWRIGHT)
        r = subprocess.run(["node", str(RAIZ / "tests" / "e2e" / "e2e_flujos.js")], env=env, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        parar(p)
