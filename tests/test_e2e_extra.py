"""Segunda prueba con navegador: historial viejo bajo demanda, Excel por la pantalla, dos ventanas y programa caído."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import FORMAS, base_inicial, op  # noqa: E402
from predio.almacen import Almacen  # noqa: E402
from predio.config import Rutas  # noqa: E402
from predio.db import BaseDeDatos  # noqa: E402
from predio.util import ahora_ms  # noqa: E402
from test_e2e import PLAYWRIGHT, RAIZ, arrancar, parar, puerto_libre  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("node") or not Path(PLAYWRIGHT).exists(), reason="falta node o playwright")


def test_historial_excel_dos_ventanas_y_programa_caido(tmp_path):
    datos = tmp_path / "Predio"
    rutas = Rutas(datos).crear()
    db = BaseDeDatos(rutas.base)
    a = Almacen(db, rutas)
    base = base_inicial()
    base["ops"].append(op("cajas", "cx1", {"id": "cx1", "abierta": ahora_ms() - 3_600_000, "fondo": 1000, "responsable": "Marcos", "cerrada": None}))
    a.aplicar(base, "Sistema", 0)
    ops, n = [], 0
    for dias_atras in (70, 50, 36, 34, 20, 10, 5, 2, 1, 0):          # 57 ventas: 42 dentro de la ventana de 35 días y 15 más viejas
        for k in range(6 if dias_atras < 35 else 5):
            n += 1
            ts = ahora_ms() - dias_atras * 86_400_000 - k * 60_000
            ops.append(op("ventas", f"v{n}", {"id": f"v{n}", "nro": n, "ts": ts, "destino": "Mostrador", "items": [], "subtotal": 100, "descuento": 0, "ajuste": None, "total": 100,
                                              "pagos": [{"metodoId": "mp1", "nombre": "Efectivo", "monto": 100}], "estado": "ok"}))
    a.aplicar({"ops": ops}, "Marcos")
    total = a.contar("ventas")
    db.cerrar()
    assert total == 57

    wb = Workbook(); ws = wb.active
    ws.append(["Producto", "Categoría", "Precio", "Stock"])
    ws.append(["Coca-Cola 500 ml", "Kiosco", 2500, 36])
    ws.append(["Gatorade 500 ml", "Kiosco", 2600, 18])
    xlsx = tmp_path / "lista.xlsx"; wb.save(xlsx)

    puerto = puerto_libre()
    p = arrancar(datos, puerto)
    try:
        env = dict(os.environ, PORT=str(puerto), PLAYWRIGHT_PATH=PLAYWRIGHT, XLSX_PATH=str(xlsx), TOTAL_VENTAS="57", EN_VENTANA="42")
        r = subprocess.run(["node", str(RAIZ / "tests" / "e2e" / "e2e_extra.js")], env=env, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        parar(p)
