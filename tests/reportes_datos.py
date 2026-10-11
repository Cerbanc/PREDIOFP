"""Datos de prueba para los reportes de la nube: resúmenes de día con la misma forma que arma predio/nube.py (armar_pedido).
Los usan las pruebas y sirven para sembrar una nube local con ~100 días realistas (ver sembrar())."""
from __future__ import annotations

import base64
import datetime as dt
import json
import random
import urllib.request

PRODUCTOS = [("Coca-Cola 500 ml", "Kiosco", 2200), ("Agua 500 ml", "Kiosco", 1500), ("Cerveza lata", "Buffet", 3200), ("Pancho", "Buffet", 3500),
             ("Hamburguesa", "Buffet", 7800), ("Papas fritas", "Buffet", 4800), ("Alfajor", "Kiosco", 1200), ("Gatorade", "Kiosco", 2600),
             ("Café", "Buffet", 1800), ("Medialuna", "Buffet", 900), ("Pelotas de pádel", "Pádel", 9000), ("Grips", "Pádel", 3000)]
DESTINOS = ["Mostrador", "Cancha de pádel", "Cancha de fútbol", "Mesa 1", "Mesa 2", "Mesa 3", "Quincho"]
RESPONSABLES = ["Ana", "Marcos", "Lucía"]
# domingo a sábado más fuerte que el resto de la semana (lunes = 0)
PESO_DIA = [0.55, 0.7, 0.75, 0.85, 1.1, 1.5, 1.35]


def resumen_dia(dia: str, semilla: int = 0, escala: float = 1.0, con_caja: bool = True, huella: str | None = None) -> dict:
    rnd = random.Random(f"{dia}:{semilla}")
    f = dt.date.fromisoformat(dia)
    tickets = max(0, int(rnd.gauss(46, 8) * PESO_DIA[f.weekday()] * escala))
    por_prod: dict[str, list[float]] = {}
    por_cat: dict[str, list[float]] = {}
    por_dest: dict[str, list[float]] = {}
    total = 0
    for _ in range(tickets):
        dest = rnd.choice(DESTINOS)
        suma = 0
        for _ in range(rnd.choice([1, 1, 2, 2, 3, 4])):
            nombre, cat, precio = rnd.choice(PRODUCTOS)
            cant = rnd.choice([1, 1, 1, 2, 3])
            suma += cant * precio
            p = por_prod.setdefault(nombre, [0, 0]); p[0] += cant; p[1] += cant * precio
            c = por_cat.setdefault(cat, [0, 0]); c[0] += cant; c[1] += cant * precio
        d = por_dest.setdefault(dest, [0, 0]); d[0] += 1; d[1] += suma
        total += suma
    descuentos = int(total * rnd.choice([0, 0.01, 0.02]))
    fiado_cargos = int(total * rnd.choice([0, 0.03, 0.06])) if tickets else 0
    fiado_pagos = rnd.choice([0, 0, 4000, 9000]) if tickets else 0
    cobrado = total - fiado_cargos + fiado_pagos
    efectivo = int(cobrado * 0.52)
    mp = int(cobrado * 0.33)
    transferencia = cobrado - efectivo - mp
    medios = [] if not tickets else [{"medio": m, "cobros": v, "devoluciones": 0, "correcciones": 0, "neto": v}
                                     for m, v in (("Efectivo", efectivo), ("MP · QR", mp), ("Transferencia", transferencia)) if v]
    cajas = []
    if con_caja and tickets:
        esperado = 15000 + efectivo
        dif = rnd.choice([0, 0, 0, 0, 0, -200, -500, 300, -1500])
        digital = [{"nombre": "MP · QR", "esperado": mp, "informado": mp + (0 if rnd.random() > 0.15 else -350), "diferencia": 0}]
        digital[0]["diferencia"] = digital[0]["informado"] - mp
        ini = int(dt.datetime.combine(f, dt.time(8, 30)).timestamp() * 1000)
        cajas.append({"abierta": ini, "cerrada": ini + 13 * 3600_000, "responsable": rnd.choice(RESPONSABLES), "fondo": 15000, "esperado": esperado, "contado": esperado + dif,
                      "diferencia": dif, "dejado": 15000, "retiroFinal": efectivo + dif, "digital": digital,
                      "nota": rnd.choice(["", "", "", "Faltaron $200 de vuelto", "Comisión del QR", "Se pagó a un proveedor"]) if dif else ""})
    top = sorted(([k, v] for k, v in por_prod.items()), key=lambda x: (-x[1][0], x[0]))[:15]
    return {
        "dia": dia, "negocio": "Predio Deportivo",
        "kpis": {"tickets": tickets, "anulados": rnd.choice([0, 0, 1]) if tickets else 0, "total_vendido": total, "descuentos": descuentos, "descuentos_total": descuentos, "descuentos_items": 0,
                 "ticket_promedio": round(total / tickets) if tickets else 0, "cobrado_neto": cobrado if tickets else 0, "cambios": 3, "cambios_importantes": 1},
        "por_medio": medios,
        "por_categoria": sorted(({"categoria": k, "unidades": v[0], "importe": v[1]} for k, v in por_cat.items()), key=lambda x: -x["importe"]),
        "por_destino": sorted(({"destino": k, "tickets": v[0], "total": v[1]} for k, v in por_dest.items()), key=lambda x: -x["total"]),
        "top_productos": [{"producto": k, "unidades": v[0], "importe": v[1]} for k, v in top],
        "fiado": {"cargos": fiado_cargos, "pagos": fiado_pagos, "anulaciones": 0},
        "movimientos_caja": {"gasto": 2500} if tickets and rnd.random() < 0.4 else {},
        "turnos_por_estado": {}, "stock_bajo": [{"producto": "Agua 500 ml", "stock": 4, "minimo": 12}] if f.day % 3 == 0 else [],
        "cajas": cajas, "cambios_importantes": [{"ts": 1, "usuario": "Caja", "resumen": "Cambio de precio"}] * 3 if tickets else [],
        "generado": 1, "huella": huella or f"h-{dia}-{semilla}",
    }


def subir(url: str, token: str, resumen: dict, xlsx: bytes = b"PK\x03\x04prueba") -> int:
    cuerpo = json.dumps({"dia": resumen["dia"], "resumen": resumen, "xlsx": base64.b64encode(xlsx).decode()}).encode()
    req = urllib.request.Request(url + "/api/subir", data=cuerpo, method="POST", headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def sembrar(url: str, token: str, hasta: dt.date, dias: int = 100, faltan: tuple[str, ...] = (), cerrados: tuple[str, ...] = ()) -> list[str]:
    """Sube `dias` días hasta `hasta` (incluido). `faltan` no se suben; `cerrados` se suben vacíos (sin movimiento). Devuelve los días subidos."""
    subidos = []
    for i in range(dias):
        f = (hasta - dt.timedelta(days=i)).isoformat()
        if f in faltan:
            continue
        subir(url, token, resumen_dia(f, escala=0 if f in cerrados else 1.0))
        subidos.append(f)
    return subidos
