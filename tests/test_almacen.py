import base64
import json
import sqlite3

import pytest

from conftest import base_inicial, borrar, op
from predio.almacen import ConflictoDeRevision, DatosInvalidos


def estado(almacen):
    return json.loads(almacen.estado_json())


def guardar(almacen, ops, usuario="Marcos", base_rev=None, formas=None):
    return almacen.aplicar({"ops": ops, "formas": formas or {}}, usuario, base_rev)


def cargar_base(almacen):
    return almacen.aplicar(base_inicial(), "Caja", 0)


def entradas(almacen, **kw):
    return [dict(r) for r in almacen.db.con.execute("SELECT * FROM auditoria ORDER BY id")]


def test_base_vacia_y_primera_carga(almacen):
    e = estado(almacen)
    assert e["vacia"] is True and e["db"] is None and e["rev"] == 0
    r = cargar_base(almacen)
    assert r["rev"] == 1
    e = estado(almacen)
    assert e["vacia"] is False and e["rev"] == 1
    db = e["db"]
    assert [p["nombre"] for p in db["productos"]] == ["Coca-Cola 500 ml", "Pancho"]
    assert db["ventas"] == [] and db["comandas"] == {"M": {"items": []}}
    assert db["pin"] == "1234" and db["negocio"] == "Predio Deportivo" and db["seq"] == 10
    assert db["config"] == {"pedirPinAjuste": True}


def test_el_orden_de_las_listas_se_conserva(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("productos", "p3", {"id": "p3", "nombre": "Agua", "catId": "c1", "tipo": "directo", "precio": 1, "stock": 0, "activo": True})])
    guardar(almacen, [op("productos", "p1", {"id": "p1", "nombre": "Coca", "catId": "c1", "tipo": "directo", "precio": 2300, "stock": 10, "activo": True})])
    nombres = [p["nombre"] for p in estado(almacen)["db"]["productos"]]
    assert nombres == ["Coca", "Pancho", "Agua"]


def test_conflicto_de_revision(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("negocio", "_", "Otro")], base_rev=1)
    with pytest.raises(ConflictoDeRevision) as exc:
        guardar(almacen, [op("negocio", "_", "Viejo")], base_rev=1)
    assert exc.value.rev_actual == 2
    assert estado(almacen)["db"]["negocio"] == "Otro"


def test_todo_o_nada_si_un_cambio_falla(almacen):
    cargar_base(almacen)
    antes = almacen.estado_json()
    ops = [
        op("productos", "p9", {"id": "p9", "nombre": "Producto-ZZZ", "catId": "c1", "tipo": "directo", "precio": 1, "stock": 0, "activo": True}),
        {"c": "no_existe", "id": "x", "d": {"a": 1}},
    ]
    with pytest.raises(DatosInvalidos):
        guardar(almacen, ops)
    assert almacen.estado_json() == antes
    assert almacen.db.con.execute("SELECT COUNT(*) FROM auditoria WHERE resumen LIKE '%Producto-ZZZ%'").fetchone()[0] == 0


def test_sin_cambios_no_sube_la_revision(almacen):
    cargar_base(almacen)
    r = guardar(almacen, [op("negocio", "_", "Predio Deportivo")])
    assert r["cambios"] == 0 and r["rev"] == 1


def test_borrar_guarda_el_registro_borrado_en_la_auditoria(almacen):
    cargar_base(almacen)
    guardar(almacen, [borrar("productos", "p1")])
    fila = [e for e in entradas(almacen) if e["accion"] == "baja"][0]
    assert fila["importante"] == 1 and "Producto eliminado: Coca-Cola" in fila["resumen"]
    assert json.loads(fila["antes"])["precio"] == 2200


def test_cambio_de_precio_es_importante_y_legible(almacen):
    cargar_base(almacen)
    p = estado(almacen)["db"]["productos"][0]
    p["precio"] = 2500
    guardar(almacen, [op("productos", "p1", p)], usuario="Marcos (con código)")
    fila = entradas(almacen)[-1]
    assert fila["usuario"] == "Marcos (con código)" and fila["importante"] == 1
    assert "precio: $ 2.200 → $ 2.500" in fila["resumen"]
    assert json.loads(fila["antes"]) == {"precio": 2200} and json.loads(fila["despues"]) == {"precio": 2500}


def test_stock_sin_movimiento_dispara_alerta(almacen):
    cargar_base(almacen)
    p = estado(almacen)["db"]["productos"][0]
    p["stock"] = 99
    guardar(almacen, [op("productos", "p1", p)])
    assert "ATENCIÓN" in entradas(almacen)[-1]["resumen"]
    # con su movimiento, en cambio, no hay alerta
    p["stock"] = 90
    mov = {"id": "mv1", "ts": 1_760_000_000_000, "prodId": "p1", "nombre": p["nombre"], "tipo": "ajuste", "cant": -9, "antes": 99, "despues": 90, "motivo": "Conteo físico"}
    guardar(almacen, [op("productos", "p1", p), op("movimientos", "mv1", mov)])
    n = len(entradas(almacen))
    guardar(almacen, [op("productos", "p1", {**p, "stock": 80}), op("movimientos", "mv2", {**mov, "id": "mv2", "cant": -10, "antes": 90, "despues": 80})])
    nuevas = [e["resumen"] for e in entradas(almacen)[n:]]
    assert len(nuevas) == 1 and "ATENCIÓN" not in nuevas[0]
    assert "Stock de Coca-Cola 500 ml: -10 (ajuste)" in nuevas[0]


def test_cuenta_de_mesa_agregar_sacar_y_vaciar(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("comandas", "m1", {"ts": 1, "items": [{"prodId": "p1", "cant": 2, "nota": ""}]})])
    assert "Cuenta Mesa 1: se agregó 2 × Coca-Cola 500 ml" in entradas(almacen)[-1]["resumen"]
    guardar(almacen, [op("comandas", "m1", {"ts": 1, "items": [{"prodId": "p1", "cant": 1, "nota": ""}]})])
    ultima = entradas(almacen)[-1]
    assert "bajó de 2 a 1" in ultima["resumen"] and ultima["importante"] == 1
    guardar(almacen, [borrar("comandas", "m1")])
    ultima = entradas(almacen)[-1]
    assert "VACIADA sin cobrar" in ultima["resumen"] and ultima["importante"] == 1


def test_cuenta_cobrada_no_se_marca_como_vaciada(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("comandas", "m1", {"ts": 1, "items": [{"prodId": "p1", "cant": 1, "nota": ""}]})])
    venta = {"id": "v1", "nro": 1, "ts": 1_760_000_000_000, "destino": "Mesa 1", "items": [], "subtotal": 2200, "descuento": 0, "ajuste": None, "total": 2200,
             "pagos": [{"metodoId": "mp1", "nombre": "Efectivo", "monto": 2200}], "estado": "ok"}
    guardar(almacen, [op("ventas", "v1", venta), borrar("comandas", "m1")])
    textos = [e["resumen"] for e in entradas(almacen)[-2:]]
    assert any("Ticket #1 cobrado: $ 2.200 · Mesa 1 · Efectivo $ 2.200" in t for t in textos)
    assert any("cobrada y cerrada" in t for t in textos)
    assert not any("VACIADA" in t for t in textos)


def test_mover_cuenta_a_otra_mesa(almacen):
    cargar_base(almacen)
    items = [{"prodId": "p1", "cant": 1, "nota": ""}]
    guardar(almacen, [op("comandas", "m1", {"ts": 1, "items": items})])
    guardar(almacen, [op("mesas", "m2", {"id": "m2", "nombre": "Mesa 2", "tipo": "mesa"}), op("comandas", "m2", {"ts": 1, "items": items}), borrar("comandas", "m1")])
    assert any("movida a Mesa 2" in e["resumen"] for e in entradas(almacen))
    assert not any("VACIADA" in e["resumen"] for e in entradas(almacen))


def test_venta_con_descuento_es_importante(almacen):
    cargar_base(almacen)
    venta = {"id": "v1", "nro": 1, "ts": 1_760_000_000_000, "destino": "Barra", "items": [], "subtotal": 1000, "descuento": 1000, "total": 0,
             "ajuste": {"tipo": "costo", "motivo": "Empleado (al costo)", "nota": "Juan"}, "pagos": [], "estado": "ok"}
    guardar(almacen, [op("ventas", "v1", venta)])
    fila = entradas(almacen)[-1]
    assert fila["importante"] == 1 and "CON DESCUENTO" in fila["resumen"] and "Juan" in fila["resumen"]


def test_anular_ticket_y_borrar_ticket(almacen):
    cargar_base(almacen)
    v = {"id": "v1", "nro": 7, "ts": 1_760_000_000_000, "destino": "Mesa 1", "items": [], "total": 500, "pagos": [], "estado": "ok"}
    guardar(almacen, [op("ventas", "v1", v)])
    guardar(almacen, [op("ventas", "v1", {**v, "estado": "anulada"})])
    assert "pasó de «ok» a «anulada»" in entradas(almacen)[-1]["resumen"] and entradas(almacen)[-1]["importante"] == 1
    guardar(almacen, [borrar("ventas", "v1")])
    assert "TICKET BORRADO" in entradas(almacen)[-1]["resumen"]


def test_turno_agendado_reprogramado_cancelado_y_borrado(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("canchas", "cn1", {"id": "cn1", "nombre": "Pádel · Techada", "precio": 26000})])
    t = {"id": "t1", "canchaId": "cn1", "fecha": "2026-10-10", "ini": 1200, "fin": 1290, "cliente": "Sofi", "estado": "reservado", "pagos": []}
    guardar(almacen, [op("turnos", "t1", t)])
    assert "Turno agendado: Pádel · Techada el 2026-10-10 a las 20:00 (Sofi)" in entradas(almacen)[-1]["resumen"]
    t2 = {**t, "ini": 1290, "fin": 1380}
    guardar(almacen, [op("turnos", "t1", t2)])
    assert "Turno reprogramado" in entradas(almacen)[-1]["resumen"] and entradas(almacen)[-1]["importante"] == 1
    guardar(almacen, [op("turnos", "t1", {**t2, "estado": "cancelado"})])
    assert "Turno cancelado" in entradas(almacen)[-1]["resumen"]
    guardar(almacen, [borrar("turnos", "t1")])
    ult = entradas(almacen)[-1]
    assert "TURNO BORRADO" in ult["resumen"] and json.loads(ult["antes"])["cliente"] == "Sofi"


def test_el_codigo_de_administracion_no_se_guarda_en_el_registro(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("pin", "_", "987654")])
    fila = entradas(almacen)[-1]
    assert fila["resumen"] == "Se cambió el código de administración" and fila["antes"] is None and fila["despues"] is None
    assert "987654" not in json.dumps(entradas(almacen))


def test_secuencia_y_contadores_no_llenan_el_registro(almacen):
    cargar_base(almacen)
    n = len(entradas(almacen))
    guardar(almacen, [op("seq", "_", 11), op("contadores", "_", {"venta": 1})])
    assert len(entradas(almacen)) == n


def test_el_registro_de_cambios_no_se_puede_tocar(almacen):
    cargar_base(almacen)
    with pytest.raises(sqlite3.DatabaseError):
        almacen.db.con.execute("DELETE FROM auditoria")
    with pytest.raises(sqlite3.DatabaseError):
        almacen.db.con.execute("UPDATE auditoria SET resumen = 'x'")


def test_imagen_se_guarda_como_archivo(almacen, rutas):
    cargar_base(almacen)
    p = estado(almacen)["db"]["productos"][0]
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()
    p["img"] = "data:image/png;base64," + png
    r = guardar(almacen, [op("productos", "p1", p)])
    assert len(r["reemplazos"]) == 1
    url = r["reemplazos"][0]["valor"]
    assert url.startswith("/img/") and url.endswith(".png")
    assert (rutas.imagenes / url.split("/")[-1]).exists()
    assert estado(almacen)["db"]["productos"][0]["img"] == url
    # la misma imagen dos veces no duplica el archivo
    p2 = dict(p); p2["id"] = "p3"; p2["nombre"] = "Otra"
    guardar(almacen, [op("productos", "p3", p2)])
    assert len(list(rutas.imagenes.glob("*.png"))) == 1


def test_imagen_invalida_se_rechaza(almacen):
    cargar_base(almacen)
    p = estado(almacen)["db"]["productos"][0]
    p["img"] = "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="
    antes = almacen.estado_json()
    with pytest.raises(DatosInvalidos):
        guardar(almacen, [op("productos", "p1", p)])
    assert almacen.estado_json() == antes


def test_vistas_legibles(almacen):
    cargar_base(almacen)
    v = {"id": "v1", "nro": 3, "ts": 1_760_000_000_000, "destino": "Mesa 1", "subtotal": 4400, "descuento": 0, "total": 4400, "estado": "ok",
         "items": [{"prodId": "p1", "nombre": "Coca-Cola 500 ml", "catId": "c1", "cant": 2, "precio": 2200, "costo": 1400, "nota": ""}],
         "pagos": [{"metodoId": "mp1", "nombre": "Efectivo", "monto": 4400}]}
    guardar(almacen, [op("ventas", "v1", v)])
    con = almacen.db.con
    fila = con.execute("SELECT * FROM v_venta_items").fetchone()
    assert (fila["producto"], fila["categoria"], fila["cantidad"], fila["subtotal"]) == ("Coca-Cola 500 ml", "Kiosco", 2, 4400)
    assert con.execute("SELECT medios_de_pago FROM v_ventas").fetchone()[0] == "Efectivo 4400"
    prod = con.execute("SELECT nombre, categoria, precio, stock FROM v_productos WHERE id = 'p1'").fetchone()
    assert tuple(prod) == ("Coca-Cola 500 ml", "Kiosco", 2200, 10)
    assert con.execute("SELECT COUNT(*) FROM v_cambios").fetchone()[0] >= 1


# ---------------------------------------------------------------- ventana de días, historia y borrado del historial
import datetime as _dt

from predio.util import ahora_ms as _ahora, dia_de as _dia


def _venta_en(dias_atras, n):
    ts = _ahora() - dias_atras * 86_400_000
    return {"id": f"v{n}", "nro": n, "ts": ts, "destino": "Mesa 1", "items": [], "subtotal": 100, "descuento": 0, "ajuste": None, "total": 100,
            "pagos": [{"metodoId": "mp1", "nombre": "Efectivo", "monto": 100}], "estado": "ok"}


def _cargar_con_historia(almacen):
    cargar_base(almacen)
    ops = []
    for d in (0, 1, 10, 40, 100):
        ops.append(op("ventas", f"v{d}", _venta_en(d, d)))
        ops.append(op("movimientos", f"m{d}", {"id": f"m{d}", "ts": _ahora() - d * 86_400_000, "prodId": "p1", "nombre": "Coca", "tipo": "venta", "cant": -1, "antes": 5, "despues": 4, "motivo": ""}))
    almacen.aplicar({"ops": ops}, "Caja")


def test_la_ventana_trae_solo_los_dias_recientes_y_todo_lo_demas(almacen):
    _cargar_con_historia(almacen)
    e = json.loads(almacen.estado_json(35))
    assert e["ventana"] == (_dt.date.today() - _dt.timedelta(days=35)).isoformat()
    assert [v["id"] for v in e["db"]["ventas"]] == ["v0", "v1", "v10"]
    assert [m["id"] for m in e["db"]["movimientos"]] == ["m0", "m1", "m10"]
    assert len(e["db"]["productos"]) == 2 and e["db"]["pin"] == "1234"          # lo que no es histórico viene completo
    completo = json.loads(almacen.estado_json(0))
    assert completo["ventana"] is None and len(completo["db"]["ventas"]) == 5


def test_historia_devuelve_lo_viejo_en_orden(almacen):
    _cargar_con_historia(almacen)
    desde = (_dt.date.today() - _dt.timedelta(days=120)).isoformat()
    hasta = (_dt.date.today() - _dt.timedelta(days=36)).isoformat()
    h = almacen.historia(desde, hasta)
    assert [v["id"] for v in h["ventas"]] == ["v40", "v100"] and [m["id"] for m in h["movimientos"]] == ["m40", "m100"]
    assert h["caja"] == [] and h["log"] == []
    with pytest.raises(DatosInvalidos):
        almacen.historia("ayer", "hoy")


def test_borrar_historial_deja_todo_en_cero_y_queda_anotado(almacen):
    _cargar_con_historia(almacen)
    almacen.aplicar({"ops": [op("contadores", "_", {"venta": 5}), op("comandas", "m1", {"ts": 1, "items": [{"prodId": "p1", "cant": 1, "nota": ""}]}),
                             op("turnos", "t1", {"id": "t1", "canchaId": "x", "fecha": "2026-10-10", "ini": 600, "fin": 660, "estado": "reservado"})]}, "Caja")
    r = almacen.borrar_historial("Marcos (con código)")
    assert r["borrados"]["ventas"] == 5 and r["borrados"]["movimientos"] == 5 and r["borrados"]["turnos"] == 1
    e = json.loads(almacen.estado_json(0))["db"]
    assert e["ventas"] == [] and e["movimientos"] == [] and e["turnos"] == [] and e["caja"] == []
    assert e["contadores"] == {"venta": 0} and e["comandas"] == {"M": {"items": []}}
    assert [p["nombre"] for p in e["productos"]] == ["Coca-Cola 500 ml", "Pancho"]          # los productos y su stock se conservan
    assert e["productos"][0]["stock"] == 10 and len(e["metodos"]) == 2 and len(e["mesas"]) == 1
    ult = entradas(almacen)[-1]
    assert ult["importante"] == 1 and "SE BORRÓ EL HISTORIAL" in ult["resumen"] and ult["usuario"] == "Marcos (con código)"
    # la pantalla puede seguir guardando con la revisión nueva
    almacen.aplicar({"ops": [op("negocio", "_", "Otro")]}, "Caja", almacen.rev())


def test_las_altas_no_duplican_el_registro_en_la_auditoria(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("ventas", "v1", _venta_en(0, 1))])
    fila = [e for e in entradas(almacen) if "Ticket #1 cobrado" in e["resumen"]][0]
    assert fila["despues"] is None
    # pero lo que se borra sí queda entero
    guardar(almacen, [borrar("ventas", "v1")])
    assert json.loads(entradas(almacen)[-1]["antes"])["nro"] == 1


def test_un_error_en_el_resumen_no_frena_el_guardado(almacen, monkeypatch):
    cargar_base(almacen)
    from predio import auditoria

    def roto(*a, **k):
        raise RuntimeError("bug futuro")

    monkeypatch.setattr(auditoria, "generar", roto)
    r = guardar(almacen, [op("ventas", "v9", _venta_en(0, 9))])
    assert r["cambios"] == 1
    assert [v["id"] for v in estado(almacen)["db"]["ventas"]] == ["v9"]
    assert "no pudo resumir" in entradas(almacen)[-1]["resumen"]


def test_cambiar_la_nota_de_un_item_no_es_sacarlo(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("comandas", "m1", {"ts": 1, "items": [{"prodId": "p1", "cant": 1, "nota": ""}]})])
    n = len(entradas(almacen))
    guardar(almacen, [op("comandas", "m1", {"ts": 1, "items": [{"prodId": "p1", "cant": 1, "nota": "bien frío"}]})])
    assert len(entradas(almacen)) == n


def test_los_telefonos_no_van_en_el_resumen(almacen):
    cargar_base(almacen)
    guardar(almacen, [op("clientes", "cl1", {"id": "cl1", "nombre": "Juan", "tel": "11 5555-0505", "tipo": "cliente"})])
    guardar(almacen, [op("clientes", "cl1", {"id": "cl1", "nombre": "Juan", "tel": "11 9999-0000", "tipo": "cliente"})])
    assert not any("5555" in e["resumen"] or "9999" in e["resumen"] for e in entradas(almacen))
