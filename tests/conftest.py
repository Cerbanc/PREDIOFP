import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from predio.almacen import Almacen  # noqa: E402
from predio.config import Rutas  # noqa: E402
from predio.db import BaseDeDatos  # noqa: E402

FORMAS = {
    "categorias": "lista", "metodos": "lista", "productos": "lista", "mesas": "lista", "ventas": "lista",
    "movimientos": "lista", "caja": "lista", "canchas": "lista", "turnos": "lista", "fijos": "lista",
    "log": "lista", "cajas": "lista", "movCaja": "lista", "clientes": "lista", "cc": "lista",
    "comandas": "mapa", "config": "unico", "negocio": "unico", "pin": "unico", "seq": "unico",
    "contadores": "unico", "v": "unico",
}


@pytest.fixture()
def rutas(tmp_path):
    return Rutas(tmp_path / "Predio").crear()


@pytest.fixture()
def db(rutas):
    base = BaseDeDatos(rutas.base)
    yield base
    base.cerrar()


@pytest.fixture()
def almacen(db, rutas):
    return Almacen(db, rutas)


def op(c, id_, d):
    return {"c": c, "id": id_, "d": d}


def borrar(c, id_):
    return {"c": c, "id": id_, "del": True}


def base_inicial():
    """Un negocio mínimo: categorías, medios de pago, mesas y un par de productos."""
    ops = [
        op("categorias", "c1", {"id": "c1", "nombre": "Kiosco", "color": "ambar"}),
        op("categorias", "c2", {"id": "c2", "nombre": "Buffet", "color": "rojo"}),
        op("metodos", "mp1", {"id": "mp1", "nombre": "Efectivo", "activo": True, "efectivo": True, "fiado": False}),
        op("metodos", "mp2", {"id": "mp2", "nombre": "MP · QR", "activo": True, "efectivo": False, "fiado": False}),
        op("mesas", "m1", {"id": "m1", "nombre": "Mesa 1", "tipo": "mesa", "x": 2, "y": 3, "w": 14, "h": 17, "rapido": False}),
        op("productos", "p1", {"id": "p1", "nombre": "Coca-Cola 500 ml", "catId": "c1", "tipo": "directo", "precio": 2200, "costo": 1400, "stock": 10, "minimo": 6, "activo": True, "paqueteUnidades": 1, "receta": []}),
        op("productos", "p2", {"id": "p2", "nombre": "Pancho", "catId": "c2", "tipo": "receta", "precio": 3500, "costo": 0, "stock": 0, "minimo": 0, "activo": True, "paqueteUnidades": 1, "receta": [{"id": "p1", "cant": 1}]}),
        op("comandas", "M", {"items": []}),
        op("config", "_", {"pedirPinAjuste": True}),
        op("negocio", "_", "Predio Deportivo"),
        op("pin", "_", "1234"),
        op("seq", "_", 10),
        op("contadores", "_", {"venta": 0}),
        op("v", "_", 7),
    ]
    for c in ("ventas", "movimientos", "caja", "canchas", "turnos", "fijos", "log", "cajas", "movCaja", "clientes", "cc"):
        pass
    return {"formas": dict(FORMAS), "ops": ops}
