"""Prueba con navegador: stock cargado leyendo la foto de un remito (con un Gemini falso).

foto -> «Leyendo el remito…» -> tabla -> corregir renglones -> cargar -> el stock cambió y quedó en el historial de movimientos."""
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import base_inicial, op  # noqa: E402
from fake_gemini import CLAVE_DE_PRUEBA, GeminiFalso  # noqa: E402
from predio.almacen import Almacen  # noqa: E402
from predio.config import Rutas  # noqa: E402
from predio.db import BaseDeDatos  # noqa: E402
from test_e2e import PLAYWRIGHT, RAIZ, arrancar, parar, puerto_libre  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("node") or not Path(PLAYWRIGHT).exists(), reason="falta node o playwright")


def item(texto, cantidad, presentacion="unidad", por=None, producto_id=None, confianza=0.0, dudoso=False):
    return {"texto": texto, "cantidad": cantidad, "presentacion": presentacion, "unidades_por_presentacion": por,
            "producto_id": producto_id, "confianza": confianza, "dudoso": dudoso}


# Lo que "lee" el Gemini falso del remito (ids de los productos que se cargan abajo)
REMITO = [
    item("COCA COLA 500CC BULTO 6X12", 2, "bulto", 72, "p1", 0.95),          # 0 seguro: 2 x 72 = 144
    item("PAN PANCHO X6", 4, "paquete", 6, "p5", 0.9),                       # 1 seguro: 24
    item("AGUA 500", 2, "pack", None, "p8", 0.9),                            # 2 amarillo: la IA dice sin gas pero el renglón no lo dice: la persona elige
    item("SERVILLETAS PAPEL X500", 2, "paquete", 500),                       # 3 rojo: no existe en el negocio
    item("ALFAJOR TRIPLE CJ X24", 1, "caja", None, "p3", 0.9),               # 4 seguro: 24 (por el X24 del renglón)
    item("SEVEN UP 500CC", 6, "unidad", None, "p11", 0.95),                  # 5 seguro: 6
    item("SPRITE 500", 12, "unidad", None, "p11", 0.9),                      # 6 amarillo: la IA eligió Seven Up, el texto dice Sprite
]
PRODUCTOS = [  # id, nombre, paquete, cómo viene, tipo, stock
    ("p3", "Alfajor triple", 24, "Caja × 24", "directo", 5), ("p4", "Salchicha", 6, "Paquete × 6", "insumo", 0), ("p5", "Pan de pancho", 6, "Paquete × 6", "insumo", 3),
    ("p6", "Coca-Cola 1,5 L", 6, "Pack × 6", "directo", 0), ("p8", "Agua sin gas 500 ml", 6, "Pack × 6", "directo", 2),
    ("p9", "Agua con gas 500 ml", 6, "Pack × 6", "directo", 0), ("p10", "Sprite 500 ml", 12, "Pack × 12", "directo", 1),
    ("p11", "Seven Up 500 ml", 12, "Pack × 12", "directo", 0), ("p12", "Turno de cancha", 1, "", "servicio", 0),
]


def tamano_jpeg(datos: bytes):
    """(ancho, alto) de un JPEG leyendo el encabezado SOF."""
    assert datos[:3] == b"\xff\xd8\xff", "no es un JPEG"
    i = 2
    while i < len(datos):
        assert datos[i] == 0xFF
        marca = datos[i + 1]
        largo = struct.unpack(">H", datos[i + 2:i + 4])[0]
        if marca in (0xC0, 0xC1, 0xC2):
            alto, ancho = struct.unpack(">HH", datos[i + 5:i + 9])
            return ancho, alto
        i += 2 + largo
    raise AssertionError("JPEG sin encabezado SOF")


def test_cargar_stock_por_foto_de_remito(tmp_path, monkeypatch):
    datos = tmp_path / "Predio"
    rutas = Rutas(datos).crear()
    db = BaseDeDatos(rutas.base)
    base = base_inicial()
    for pid, nombre, paq, pn, tipo, stock in PRODUCTOS:
        base["ops"].append(op("productos", pid, {"id": pid, "nombre": nombre, "catId": "c1", "tipo": tipo, "precio": 1000, "costo": 500, "stock": stock, "minimo": 2,
                                                 "activo": True, "paqueteUnidades": paq, "paqueteNombre": pn, "receta": []}))
    Almacen(db, rutas).aplicar(base, "Sistema", 0)
    db.cerrar()
    # sin clave todavía: la prueba la "pega" en el archivo a mitad de camino, como lo haría la persona
    rutas.ajustes.write_text(json.dumps({"gemini_clave": ""}), encoding="utf-8")

    gemini = GeminiFalso(REMITO).iniciar()
    gemini.retardo = 1.2
    monkeypatch.setenv("PREDIO_API_GEMINI", gemini.url)
    puerto = puerto_libre()
    p = arrancar(datos, puerto)
    fotos = Path(os.environ.get("E2E_FOTOS", "")) if os.environ.get("E2E_FOTOS") else None
    try:
        env = dict(os.environ, PORT=str(puerto), PLAYWRIGHT_PATH=PLAYWRIGHT, DATOS=str(datos), CLAVE=CLAVE_DE_PRUEBA, GEMINI_FALSO=gemini.url.rsplit("/v1beta", 1)[0],
                   FOTOS=str(fotos or ""))
        r = subprocess.run(["node", str(RAIZ / "tests" / "e2e" / "e2e_remito.js")], env=env, capture_output=True, text=True, timeout=240)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        parar(p)
        gemini.parar()

    # ---- lo que viajó a Google: la foto REDUCIDA (JPEG, lado mayor 1600) y la clave sólo en el encabezado
    leidos = [x for x in gemini.pedidos if x["ruta"].endswith(":generateContent")]
    assert len(leidos) >= 4
    for x in leidos:
        assert x["cabeceras"]["x-goog-api-key"] == CLAVE_DE_PRUEBA
        assert CLAVE_DE_PRUEBA not in x["ruta"] and CLAVE_DE_PRUEBA not in json.dumps(x["cuerpo"])
    import base64
    imagenes = [base64.b64decode(next(q["inline_data"]["data"] for q in x["cuerpo"]["contents"][0]["parts"] if "inline_data" in q)) for x in leidos]
    assert tamano_jpeg(imagenes[0]) == (1600, 1200), "la foto de 3200 x 2400 se redujo a 1600 x 1200"
    assert len(imagenes[0]) < 1_500_000
    assert imagenes[0] == imagenes[1], "la misma foto reducida dos veces da los mismos bytes (así se detecta el remito repetido)"
    # ---- la clave no quedó en ningún lado
    registro = (datos / "logs_tecnicos" / "predio.log").read_text(encoding="utf-8", errors="replace")
    assert CLAVE_DE_PRUEBA not in registro and "Remito leído" in registro
    for f in list(datos.rglob("*")):
        if f.is_file() and f.name != "configuracion.json":
            assert CLAVE_DE_PRUEBA.encode() not in f.read_bytes(), f
    # ---- el stock quedó guardado en la base (no sólo en pantalla) y en el historial
    con = sqlite3.connect(f"file:{rutas.base}?mode=ro", uri=True)
    try:
        prods = {json.loads(d)["id"]: json.loads(d) for (d,) in con.execute("SELECT data FROM docs WHERE coleccion='productos'")}
        movs = [json.loads(d) for (d,) in con.execute("SELECT data FROM docs WHERE coleccion='movimientos'")]
        cambios = [r[0] for r in con.execute("SELECT resumen FROM auditoria WHERE coleccion='movimientos'")]
    finally:
        con.close()
    remitos = [m for m in movs if str(m.get("ref") or "").startswith("remito:")]
    assert len(remitos) == 9 and all(m["motivo"].startswith("Remito (foto) · ") and m["tipo"] == "ingreso" for m in remitos)
    assert not any("SERVILLETAS" in m["motivo"] for m in remitos), "lo que no tenía producto no se cargó"
    # stock inicial: p1 10, p3 5, p5 3, p9 0, p10 1, p11 0. Primera carga: 144, 24, 30, 12, 12, 8. Segunda (el mismo remito, a propósito): 144, 24, 6.
    assert {k: prods[k]["stock"] for k in ("p1", "p3", "p5", "p9", "p10", "p11")} == {"p1": 298, "p3": 53, "p5": 33, "p9": 12, "p10": 13, "p11": 14}
    assert prods["p8"]["stock"] == 2 and prods["p4"]["stock"] == 0 and prods["p12"]["stock"] == 0
    assert any("Remito (foto) · COCA COLA 500CC" in c for c in cambios), cambios[:5]
