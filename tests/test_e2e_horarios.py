"""Prueba con navegador: horarios reales de las canchas (base nueva y base ya existente que se migra) y avisos de turno con pádel hasta las 23:30."""
import datetime as dt
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


def correr(fase, datos):
    puerto = puerto_libre()
    p = arrancar(datos, puerto)
    try:
        env = dict(os.environ, PORT=str(puerto), PLAYWRIGHT_PATH=PLAYWRIGHT, FASE=fase)
        r = subprocess.run(["node", str(RAIZ / "tests" / "e2e" / "e2e_horarios.js")], env=env, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        parar(p)


def cancha(id_, clave, nombre, deporte, dur, ap, precio, precio_fijo):
    """Como las dejaba la versión anterior: horarios de ejemplo (abre 10:00 o 8:00, cierra a medianoche)."""
    return op("canchas", id_, {"id": id_, "clave": clave, "nombre": nombre, "deporte": deporte, "dur": dur, "ap": ap, "ci": 1440, "precio": precio, "precioFijo": precio_fijo, "lugarId": None})


def turno(id_, cancha_id, fecha, ini, cliente, estado="reservado"):
    return op("turnos", id_, {"id": id_, "canchaId": cancha_id, "fecha": fecha, "ini": ini, "fin": ini + 60, "cliente": cliente, "tel": "", "nota": "", "precio": 31000, "estado": estado,
                              "pagos": [], "ventaId": None, "esFijo": False, "fijoId": None, "ocurrencia": None, "clienteId": None, "tipoPrecio": "comun"})


def test_base_nueva_trae_los_horarios_de_papel(tmp_path):
    correr("nueva", tmp_path / "Predio")


def test_base_existente_se_migra_una_sola_vez_sin_tocar_los_precios(tmp_path):
    datos = tmp_path / "Predio"
    rutas = Rutas(datos).crear()
    db = BaseDeDatos(rutas.base)
    a = Almacen(db, rutas)
    a.aplicar(base_inicial(), "Sistema", 0)
    manana = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    a.aplicar({"ops": [
        cancha("cn_f5a", "f5a", "Fútbol 5 · Cancha 1", "futbol", 60, 600, 31000, 26000), cancha("cn_f5b", "f5b", "Fútbol 5 · Cancha 2", "futbol", 60, 600, 31000, 26000),
        cancha("cn_f7", "f7", "Fútbol 7 · Cancha completa", "futbol", 60, 600, 50000, 45000), cancha("cn_pT", "padelT", "Pádel · Techada", "padel", 90, 480, 27000, 23000),
        cancha("cn_pA", "padelA", "Pádel · Abierta", "padel", 90, 480, 23000, 20000),
        turno("t1", "cn_f5b", manana, 600, "Temprano"),                                  # 10:00: antes de que abra el fútbol nuevo (14:00)
        turno("t2", "cn_f7", manana, 1380, "Noche"),                                     # 23:00 a 24:00: después de que cierra (23:00)
        turno("t3", "cn_f5a", manana, 420, "Cancelado de las 7", estado="cancelado"),    # un turno cancelado no se ve: no amplía nada
        op("fijos", "fx1", {"id": "fx1", "canchaId": "cn_pA", "dow": 2, "ini": 1290, "cliente": "Fijo de pádel", "tel": "", "nota": "", "desde": "2026-01-01", "hasta": None, "clienteId": None}),
        op("cajas", "cx1", {"id": "cx1", "abierta": ahora_ms() - 3_600_000, "fondo": 1000, "responsable": "Marcos", "cerrada": None}),
    ]}, "Sistema", None)
    db.cerrar()
    correr("migracion", datos)
