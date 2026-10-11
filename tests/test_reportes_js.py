"""Cálculo de los reportes por período de la nube (nube/src/reportes.js), probado con node y datos armados a mano. Se salta si no hay node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="falta node")

PRUEBA = r"""
import { agregarPeriodo, diaValido, cuentaDias, sumarDias, MAX_DIAS } from "./reportes.mjs";
const f = JSON.parse(await new Promise((ok) => { let t = ""; process.stdin.on("data", (c) => (t += c)); process.stdin.on("end", () => ok(t)); }));
const filas = f.filas.map((x) => ({ dia: x.dia, subido: x.subido ?? 1000, r: x.r }));
const out = { periodo: agregarPeriodo(filas, f.desde, f.hasta, f.hoy) };
out.utiles = { v1: diaValido("2026-02-31"), v2: diaValido("2026-02-28"), v3: diaValido("2026-2-8"), c: cuentaDias("2026-10-01", "2026-10-31"), s: sumarDias("2026-02-28", 1), tope: MAX_DIAS };
console.log(JSON.stringify(out));
"""


def dia(total=0, tickets=0, cajas=None, medios=None, cats=None, prods=None, dests=None, fiado=None, cobrado=None, descuentos=0, anulados=0, movs=None, stock=None):
    return {
        "kpis": {"total_vendido": total, "tickets": tickets, "descuentos": descuentos, "anulados": anulados, "cobrado_neto": total if cobrado is None else cobrado, "ticket_promedio": 0},
        "por_medio": medios or [], "por_categoria": cats or [], "por_destino": dests or [], "top_productos": prods or [],
        "fiado": fiado or {"cargos": 0, "pagos": 0, "anulaciones": 0}, "movimientos_caja": movs or {}, "stock_bajo": stock or [], "cajas": cajas or [],
    }


def caja(abierta, resp="Ana", cerrada=True, dif=0, digital=None, nota=""):
    return {"abierta": abierta, "cerrada": abierta + 1000 if cerrada else None, "responsable": resp, "fondo": 100, "esperado": 1000, "contado": 1000 + dif, "diferencia": dif, "nota": nota, "digital": digital or []}


def calcular(tmp_path, filas, desde, hasta, hoy):
    (tmp_path / "reportes.mjs").write_text((RAIZ / "nube" / "src" / "reportes.js").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "prueba.mjs").write_text(PRUEBA, encoding="utf-8")
    r = subprocess.run(["node", "prueba.mjs"], cwd=tmp_path, input=json.dumps({"filas": filas, "desde": desde, "hasta": hasta, "hoy": hoy}), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_distingue_dia_con_datos_sin_movimiento_y_sin_datos(tmp_path):
    filas = [{"dia": "2026-10-05", "r": dia(1000, 4)}, {"dia": "2026-10-06", "r": dia()}, {"dia": "2026-10-08", "r": dia(3000, 9)}]
    p = calcular(tmp_path, filas, "2026-10-05", "2026-10-11", "2026-10-09")["periodo"]
    assert [d["estado"] for d in p["dias"]] == ["datos", "vacio", "falta", "datos", "falta", "futuro", "futuro"]
    t = p["totales"]
    assert (t["dias_con_datos"], t["dias_vacios"], t["dias_sin_datos"], t["dias_futuros"], t["dias_total"]) == (2, 1, 2, 2, 5)
    assert t["total_vendido"] == 4000 and t["tickets"] == 13 and t["ticket_promedio"] == round(4000 / 13)
    assert "total" not in p["dias"][2] and "total" not in p["dias"][5]          # lo que falta no tiene número: no se cuenta como cero


def test_suma_medios_categorias_destinos_productos_y_fiado(tmp_path):
    a = dia(1000, 2, medios=[{"medio": "Efectivo", "cobros": 600, "devoluciones": 0, "correcciones": 0, "neto": 600}, {"medio": "MP", "cobros": 400, "devoluciones": 0, "correcciones": 0, "neto": 400}],
            cats=[{"categoria": "Kiosco", "unidades": 3, "importe": 700}, {"categoria": "Buffet", "unidades": 1, "importe": 300}],
            prods=[{"producto": "Coca", "unidades": 3, "importe": 700}, {"producto": "Pancho", "unidades": 1, "importe": 300}],
            dests=[{"destino": "Mesa 1", "tickets": 2, "total": 1000}], fiado={"cargos": 100, "pagos": 50, "anulaciones": 0}, movs={"gasto": 10}, descuentos=20)
    b = dia(500, 1, medios=[{"medio": "Efectivo", "cobros": 600, "devoluciones": -100, "correcciones": 0, "neto": 500}],
            cats=[{"categoria": "Kiosco", "unidades": 2, "importe": 500}], prods=[{"producto": "Coca", "unidades": 2, "importe": 500}, {"producto": "Alfajor", "unidades": 5, "importe": 50}],
            dests=[{"destino": "Mesa 1", "tickets": 1, "total": 500}, {"destino": "Quincho", "tickets": 0, "total": 0}], fiado={"cargos": 0, "pagos": 70, "anulaciones": 30}, movs={"gasto": 5, "retiro": 99}, descuentos=5, anulados=1)
    p = calcular(tmp_path, [{"dia": "2026-10-01", "r": a}, {"dia": "2026-10-02", "r": b}], "2026-10-01", "2026-10-02", "2026-10-02")["periodo"]
    assert {x["medio"]: (x["cobros"], x["devoluciones"], x["neto"]) for x in p["por_medio"]} == {"Efectivo": (1200, -100, 1100), "MP": (400, 0, 400)}
    assert p["por_medio"][0]["medio"] == "Efectivo"                                    # ordenado de más a menos
    assert [(x["categoria"], x["unidades"], x["importe"]) for x in p["por_categoria"]] == [("Kiosco", 5, 1200), ("Buffet", 1, 300)]
    assert [(x["producto"], x["unidades"]) for x in p["top_productos"]] == [("Alfajor", 5), ("Coca", 5), ("Pancho", 1)]     # empate de unidades: por nombre
    assert p["por_destino"][0] == {"destino": "Mesa 1", "tickets": 3, "total": 1500}
    assert p["fiado"] == {"cargos": 100, "pagos": 120, "anulaciones": 30} and p["movimientos_caja"] == {"gasto": 15, "retiro": 99}
    t = p["totales"]
    assert (t["descuentos"], t["anulados"], t["cobrado_neto"]) == (25, 1, 1500)


def test_mejor_y_peor_dia_ignoran_dias_sin_ventas_o_sin_datos(tmp_path):
    filas = [{"dia": "2026-10-01", "r": dia(500, 3)}, {"dia": "2026-10-02", "r": dia()}, {"dia": "2026-10-04", "r": dia(900, 5)}, {"dia": "2026-10-05", "r": dia(100, 1)}]
    p = calcular(tmp_path, filas, "2026-10-01", "2026-10-05", "2026-10-05")["periodo"]
    assert p["mejor"]["dia"] == "2026-10-04" and p["peor"]["dia"] == "2026-10-05"
    assert p["totales"]["promedio_dia"] == 500
    p = calcular(tmp_path, filas[:1], "2026-10-01", "2026-10-05", "2026-10-05")["periodo"]
    assert p["mejor"]["dia"] == "2026-10-01" and p["peor"] is None               # con un solo día con ventas no hay "más flojo"


def test_cierres_de_caja_cuentan_los_dias_con_diferencia_y_no_repiten_la_caja_que_cruza_la_medianoche(tmp_path):
    noche = caja(1_000_000, "Marcos", dif=-200)
    filas = [
        {"dia": "2026-10-01", "r": dia(1000, 2, cajas=[caja(100, "Ana"), caja(500, "Lucía", dif=300, nota="Se pagó un proveedor")])},
        {"dia": "2026-10-02", "r": dia(1000, 2, cajas=[noche])},
        {"dia": "2026-10-03", "r": dia(1000, 2, cajas=[noche, caja(5_000_000, "Ana", digital=[{"nombre": "MP", "esperado": 100, "informado": 50, "diferencia": -50}])])},   # la caja de la noche se ve también acá
        {"dia": "2026-10-04", "r": dia(1000, 2, cajas=[caja(9_000_000, "Ana", cerrada=False)])},
    ]
    p = calcular(tmp_path, filas, "2026-10-01", "2026-10-04", "2026-10-04")["periodo"]
    c = p["cierres_resumen"]
    assert c == {"cajas": 5, "cerradas": 4, "abiertas": 1, "con_diferencia": 3, "dias_con_cierre": 3, "dias_con_diferencia": 3}
    assert [(x["dia"], x["responsable"]) for x in p["cierres"]] == [("2026-10-04", "Ana"), ("2026-10-03", "Ana"), ("2026-10-02", "Marcos"), ("2026-10-01", "Lucía"), ("2026-10-01", "Ana")]
    por_dia = {d["dia"]: (d["dif"], d["dif_dig"]) for d in p["dias"]}
    assert por_dia == {"2026-10-01": (300, 0), "2026-10-02": (-200, 0), "2026-10-03": (0, -50), "2026-10-04": (0, 0)}      # la diferencia de la caja de la noche se anota una vez
    assert [x for x in p["cierres"] if x["nota"]][0]["nota"] == "Se pagó un proveedor"


def test_una_caja_vista_abierta_y_despues_cerrada_queda_cerrada(tmp_path):
    filas = [{"dia": "2026-10-01", "r": dia(10, 1, cajas=[caja(100, "Ana", cerrada=False)])}, {"dia": "2026-10-02", "r": dia(10, 1, cajas=[caja(100, "Ana", dif=-5)])}]
    p = calcular(tmp_path, filas, "2026-10-01", "2026-10-02", "2026-10-02")["periodo"]
    assert len(p["cierres"]) == 1 and p["cierres"][0]["cerrada"] and p["cierres"][0]["dia"] == "2026-10-01" and p["cierres"][0]["diferencia"] == -5


def test_una_fila_ilegible_cuenta_como_dia_que_falta_y_el_stock_es_el_del_ultimo_dia(tmp_path):
    filas = [{"dia": "2026-10-01", "r": dia(10, 1, stock=[{"producto": "A", "stock": 1, "minimo": 5}])}, {"dia": "2026-10-02", "r": None},
             {"dia": "2026-10-03", "r": dia(10, 1, stock=[{"producto": "B", "stock": 0, "minimo": 5}])}]
    p = calcular(tmp_path, filas, "2026-10-01", "2026-10-03", "2026-10-03")["periodo"]
    assert p["dias"][1]["estado"] == "falta" and p["stock_bajo"] == {"dia": "2026-10-03", "lista": [{"producto": "B", "stock": 0, "minimo": 5}]}
    assert p["ultimo_dia"] == "2026-10-03"


def test_resumenes_viejos_o_incompletos_no_rompen(tmp_path):
    filas = [{"dia": "2026-10-01", "r": {"kpis": {"total_vendido": "1500", "tickets": 3}}}, {"dia": "2026-10-02", "r": {}}]
    p = calcular(tmp_path, filas, "2026-10-01", "2026-10-02", "2026-10-02")["periodo"]
    assert p["totales"]["total_vendido"] == 1500 and p["dias"][1]["estado"] == "vacio" and p["cierres"] == []


def test_fechas_y_tope(tmp_path):
    u = calcular(tmp_path, [], "2026-10-01", "2026-10-01", "2026-10-01")["utiles"]
    assert u == {"v1": False, "v2": True, "v3": False, "c": 31, "s": "2026-03-01", "tope": 400}


def test_un_ano_de_datos_se_calcula_rapido(tmp_path):
    import datetime as dt
    ini = dt.date(2026, 1, 1)
    filas = [{"dia": (ini + dt.timedelta(days=i)).isoformat(), "r": dia(1000 + i, 5, cajas=[caja(1000 + i)], prods=[{"producto": f"P{i % 40}", "unidades": 2, "importe": 100}])} for i in range(365)]
    p = calcular(tmp_path, filas, "2026-01-01", "2026-12-31", "2026-12-31")["periodo"]
    assert p["totales"]["dias_con_datos"] == 365 and len(p["dias"]) == 365 and len(p["top_productos"]) == 20
