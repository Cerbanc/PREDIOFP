"""Registro de cambios automático.

Cada vez que se guarda algo, el programa compara cómo estaba con cómo quedó y deja una línea
en la tabla `auditoria`, sin importar qué pantalla lo hizo. Así nada cambia sin dejar rastro:

  - quién lo hizo (la persona responsable de la caja en ese momento) y cuándo
  - qué cambió, en una frase legible
  - cómo estaba antes y cómo quedó (si algo se borra, el registro borrado queda guardado acá)

Los cambios importantes (borrados, anulaciones, descuentos, plata, precios, turnos cancelados) van marcados.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .util import hm, plata

FEMENINOS = {"categorias", "canchas", "cc", "config", "mesas"}


def _g(col: str, masculino: str, femenino: str) -> str:
    return femenino if col in FEMENINOS else masculino


# Cosas técnicas que cambian solas todo el tiempo y no son decisiones de nadie.
IGNORAR_COLECCIONES = {"seq", "contadores", "v"}
# En las altas no se repite el registro entero (ya está guardado); estas guardan igual su detalle porque es chico y útil.
ALTA_CON_DETALLE = {"comandas"}
LIMITE_JSON = 60_000

ETIQUETAS = {
    "categorias": "Categoría", "metodos": "Medio de pago", "productos": "Producto", "mesas": "Mesa o lugar del salón",
    "canchas": "Cancha", "turnos": "Turno", "fijos": "Turno fijo", "clientes": "Cliente", "cc": "Cuenta corriente",
    "cajas": "Caja", "movCaja": "Movimiento de caja", "caja": "Libro de caja", "movimientos": "Stock",
    "ventas": "Ticket", "comandas": "Cuenta", "log": "Registro", "config": "Configuración",
    "negocio": "Nombre del negocio", "pin": "Código de administración",
}
CAMPOS = {
    "nombre": "nombre", "catId": "categoría", "tipo": "tipo", "precio": "precio", "costo": "costo", "stock": "stock",
    "minimo": "mínimo", "paqueteNombre": "paquete", "paqueteUnidades": "unidades por paquete", "activo": "activo",
    "receta": "receta", "img": "imagen", "link": "link", "precioFijo": "precio fijo", "dur": "duración", "ap": "abre",
    "ci": "cierra", "lugarId": "lugar", "tel": "teléfono", "nota": "nota", "efectivo": "es efectivo", "fiado": "es fiado",
    "color": "color", "x": "posición x", "y": "posición y", "w": "ancho", "h": "alto", "rapido": "acceso rápido",
    "fecha": "fecha", "ini": "hora de inicio", "fin": "hora de fin", "canchaId": "cancha", "cliente": "a nombre de",
    "estado": "estado", "tipoPrecio": "tipo de precio", "clienteId": "cliente", "hasta": "hasta", "desde": "desde",
    "pedirPinAjuste": "pedir código para descuentos", "paisWa": "código de país (WhatsApp)", "msgTurno": "mensaje de turno",
    "deporte": "deporte", "dow": "día de la semana", "monto": "monto", "motivo": "motivo",
}
DINERO = {"precio", "costo", "precioFijo", "monto", "fondo", "total", "subtotal", "descuento"}
IDS_A_NOMBRE = {"catId": "categorias", "canchaId": "canchas", "lugarId": "mesas", "clienteId": "clientes", "metodoId": "metodos"}
# En estos campos, un cambio es "importante" (afecta plata, ventas o la operación)
IMPORTANTES = {
    "productos": {"nombre", "precio", "costo", "tipo", "activo", "receta", "catId", "paqueteUnidades"},
    "canchas": {"precio", "precioFijo", "dur", "ap", "ci", "nombre", "lugarId"},
    "metodos": {"activo", "nombre", "efectivo", "fiado"},
    "config": {"pedirPinAjuste"},
    "fijos": {"hasta", "ini", "canchaId", "dow"},
    "clientes": set(),
    "mesas": set(),
    "categorias": {"nombre"},
}


@dataclass
class Entrada:
    coleccion: str
    ref: str
    accion: str          # alta | cambio | baja
    importante: bool
    resumen: str
    antes: Any = None
    despues: Any = None


def _json(valor: Any) -> str | None:
    if valor is None:
        return None
    t = json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    if len(t) > LIMITE_JSON:
        return json.dumps({"_demasiado_grande": True, "caracteres": len(t)})
    return t


class Contexto:
    """Ayuda a traducir ids a nombres usando lo que ya quedó guardado (y lo que se borró en este mismo guardado)."""

    def __init__(self, con: sqlite3.Connection, cambios: list[tuple[str, str, dict | None, dict | None]]):
        self.con = con
        self.cambios = cambios
        self._cache: dict[tuple[str, str], dict | None] = {}
        self.previos = {(c, i): a for c, i, a, _ in cambios if a is not None}
        self.ventas_nuevas = [d for c, _, a, d in cambios if c == "ventas" and a is None and d]

    def doc(self, coleccion: str, id_: str) -> dict | None:
        clave = (coleccion, str(id_))
        if clave not in self._cache:
            fila = self.con.execute("SELECT data FROM docs WHERE coleccion = ? AND id = ?", clave).fetchone()
            val = json.loads(fila[0]) if fila else self.previos.get(clave)
            self._cache[clave] = val if isinstance(val, dict) else None
        return self._cache[clave]

    def nombre(self, coleccion: str, id_: Any, vacio: str = "(sin dato)") -> str:
        if not id_:
            return vacio
        d = self.doc(coleccion, str(id_))
        return str(d.get("nombre")) if d and d.get("nombre") else "(borrado)"

    def producto(self, id_: Any) -> str:
        return self.nombre("productos", id_, "(producto)")

    def lugar(self, clave: str) -> str:
        if clave == "M":
            return "Mostrador"
        return self.nombre("mesas", clave, str(clave))

    def cancha(self, id_: Any) -> str:
        return self.nombre("canchas", id_, "(cancha)")

    def turno_texto(self, turno_id: str) -> str:
        t = self.doc("turnos", turno_id)
        if not t:
            return "Turno"
        return f"Turno {self.cancha(t.get('canchaId'))} {hm(t.get('ini'))}"

    def valor(self, campo: str, v: Any) -> str:
        if campo == "tel":                      # los teléfonos no van en frases que terminan en archivos de texto o Excel
            return "cambiado" if v else "vacío"
        if campo in IDS_A_NOMBRE:
            return self.nombre(IDS_A_NOMBRE[campo], v, "ninguno")
        if campo in DINERO and isinstance(v, (int, float)):
            return plata(v)
        if campo in ("ini", "fin", "ap", "ci") and isinstance(v, (int, float)):
            return hm(v)
        if isinstance(v, bool):
            return "sí" if v else "no"
        if v is None or v == "":
            return "vacío"
        if isinstance(v, list):
            if campo == "receta":
                return " + ".join(f"{r.get('cant', 1)} × {self.producto(r.get('id'))}" for r in v) or "vacía"
            return f"{len(v)} elementos"
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)[:80]
        if campo == "img":
            return "imagen nueva"
        return str(v)


def _cambiados(antes: dict, despues: dict, ignorar: set[str] = frozenset()) -> dict[str, tuple[Any, Any]]:
    claves = set(antes) | set(despues)
    return {k: (antes.get(k), despues.get(k)) for k in sorted(claves) if k not in ignorar and antes.get(k) != despues.get(k)}


def _antes_despues(cambiados: dict[str, tuple[Any, Any]]) -> tuple[dict, dict]:
    return {k: a for k, (a, _) in cambiados.items()}, {k: d for k, (_, d) in cambiados.items()}


def _nombre_generico(d: dict | None, id_: str) -> str:
    if not d:
        return id_
    for k in ("nombre", "cliente", "concepto", "motivo"):
        if d.get(k):
            return str(d[k])
    return id_


# ---------------------------------------------------------------- resúmenes por tipo de dato

def _generico(ctx: Contexto, col: str, id_: str, a: dict | None, d: dict | None) -> list[Entrada]:
    etiqueta = ETIQUETAS.get(col, col)
    if a is None:
        return [Entrada(col, id_, "alta", False, f"{etiqueta} {_g(col, 'nuevo', 'nueva')}: {_nombre_generico(d, id_)}", None, d)]
    if d is None:
        return [Entrada(col, id_, "baja", True, f"{etiqueta} {_g(col, 'eliminado', 'eliminada')}: {_nombre_generico(a, id_)}", a, None)]
    cam = _cambiados(a, d)
    if not cam:
        return []
    imp_campos = IMPORTANTES.get(col, set())
    partes = [f"{CAMPOS.get(k, k)}: {ctx.valor(k, x)} → {ctx.valor(k, y)}" for k, (x, y) in cam.items()]
    imp = any(k in imp_campos for k in cam)
    ant, des = _antes_despues(cam)
    return [Entrada(col, id_, "cambio", imp, f"{etiqueta} {_nombre_generico(d, id_)}: " + "; ".join(partes), ant, des)]


def _producto(ctx, col, id_, a, d):
    if a is None:
        stock = d.get("stock") or 0
        extra = f", stock inicial {stock}" if stock else ""
        return [Entrada(col, id_, "alta", True, f"Producto nuevo: {d.get('nombre')} ({ctx.nombre('categorias', d.get('catId'))}) a {plata(d.get('precio'))}{extra}", None, d)]
    if d is None:
        return [Entrada(col, id_, "baja", True, f"Producto eliminado: {a.get('nombre')}", a, None)]
    cam = _cambiados(a, d)
    if "stock" in cam:
        # El stock cambia siempre junto con un movimiento de stock. Si no lo hay, es una señal de alerta.
        hay_mov = any(c == "movimientos" and (dd or {}).get("prodId") == id_ for c, _, _, dd in ctx.cambios)
        if hay_mov:
            cam.pop("stock")
    if not cam:
        return []
    sin_mov = "stock" in cam
    partes = [f"{CAMPOS.get(k, k)}: {ctx.valor(k, x)} → {ctx.valor(k, y)}" for k, (x, y) in cam.items()]
    txt = f"Producto {d.get('nombre')}: " + "; ".join(partes)
    if sin_mov:
        txt = "ATENCIÓN, el stock cambió sin registrar un movimiento. " + txt
    imp = sin_mov or any(k in IMPORTANTES["productos"] for k in cam)
    ant, des = _antes_despues(cam)
    return [Entrada(col, id_, "cambio", imp, txt, ant, des)]


def _venta(ctx, col, id_, a, d):
    if a is None:
        medios = ", ".join(f"{p.get('nombre')} {plata(p.get('monto'))}" for p in d.get("pagos", []))
        txt = f"Ticket #{d.get('nro')} cobrado: {plata(d.get('total'))} · {d.get('destino')} · {medios}"
        imp = bool(d.get("ajuste"))
        if imp:
            aj = d["ajuste"]
            txt += f" · CON DESCUENTO de {plata(d.get('descuento'))} ({aj.get('motivo')}{' · ' + aj['nota'] if aj.get('nota') else ''})"
        return [Entrada(col, id_, "alta", imp, txt, None, d)]
    if d is None:
        return [Entrada(col, id_, "baja", True, f"TICKET BORRADO: #{a.get('nro')} por {plata(a.get('total'))} ({a.get('destino')}). No debería pasar: los tickets se anulan, no se borran.", a, None)]
    cam = _cambiados(a, d)
    if not cam:
        return []
    ant, des = _antes_despues(cam)
    if a.get("estado") != d.get("estado"):
        return [Entrada(col, id_, "cambio", True, f"Ticket #{d.get('nro')} pasó de «{a.get('estado')}» a «{d.get('estado')}» ({plata(d.get('total'))}, {d.get('destino')})", ant, des)]
    partes = []
    if "pagos" in cam:
        partes.append("medios de pago: " + ", ".join(f"{p.get('nombre')} {plata(p.get('monto'))}" for p in d.get("pagos", [])))
    if "nota" in cam:
        partes.append(f"nota: «{a.get('nota') or ''}» → «{d.get('nota') or ''}»")
    resto = [k for k in cam if k not in ("pagos", "nota", "corregido")]
    partes += [f"{CAMPOS.get(k, k)} modificado" for k in resto]
    return [Entrada(col, id_, "cambio", True, f"Ticket #{d.get('nro')} corregido · " + ("; ".join(partes) or "marcado como corregido"), ant, des)]


def _libro_caja(ctx, col, id_, a, d):
    if a is None:
        tipo = {"cobro": "Cobro", "devolucion": "Devolución", "correccion": "Corrección"}.get(d.get("tipo"), d.get("tipo"))
        return [Entrada(col, id_, "alta", d.get("tipo") != "cobro", f"{tipo} de {plata(d.get('monto'))} en {d.get('nombre')}: {d.get('motivo')}", None, d)]
    r = _generico(ctx, col, id_, a, d)
    for e in r:
        e.importante = True
        e.resumen = "El libro de caja no debería modificarse. " + e.resumen
    return r


def _caja_sesion(ctx, col, id_, a, d):
    if a is None:
        return [Entrada(col, id_, "alta", True, f"Caja abierta por {d.get('responsable')} con {plata(d.get('fondo'))} de cambio", None, d)]
    if d is None:
        return [Entrada(col, id_, "baja", True, f"Caja BORRADA (abierta por {a.get('responsable')})", a, None)]
    cam = _cambiados(a, d)
    if not cam:
        return []
    ant, des = _antes_despues(cam)
    if not a.get("cerrada") and d.get("cerrada"):
        dif = d.get("diferencia") or 0
        txt = (f"Caja cerrada por {d.get('responsable')}: esperado {plata(d.get('esperado'))}, contado {plata(d.get('contado'))}, "
               f"{'sin diferencia' if dif == 0 else ('sobran ' if dif > 0 else 'faltan ') + plata(abs(dif))}. "
               f"Retiro a caja mayor {plata(d.get('retiroFinal'))}, quedan {plata(d.get('dejado'))} de cambio")
        return [Entrada(col, id_, "cambio", True, txt, ant, des)]
    return _generico(ctx, col, id_, a, d)


def _mov_caja(ctx, col, id_, a, d):
    nombres = {"gasto": "Gasto", "proveedor": "Pago a proveedor", "retiro": "Retiro a caja mayor", "ingreso": "Ingreso de efectivo"}
    if a is None:
        quien = f" · {d.get('proveedor')}" if d.get("proveedor") else ""
        return [Entrada(col, id_, "alta", True, f"{nombres.get(d.get('tipo'), d.get('tipo'))}: {plata(d.get('monto'))} · {d.get('concepto')}{quien}", None, d)]
    if d is None:
        return [Entrada(col, id_, "baja", True, f"Movimiento de caja BORRADO: {nombres.get(a.get('tipo'), a.get('tipo'))} {plata(a.get('monto'))} · {a.get('concepto')}", a, None)]
    r = _generico(ctx, col, id_, a, d)
    for e in r:
        e.importante = True
    return r


def _mov_stock(ctx, col, id_, a, d):
    if a is None:
        tipo = d.get("tipo")
        signo = "+" if (d.get("cant") or 0) > 0 else ""
        txt = f"Stock de {d.get('nombre')}: {signo}{d.get('cant')} ({tipo}) de {d.get('antes')} a {d.get('despues')} · {d.get('motivo') or ''}".rstrip(" ·")
        return [Entrada(col, id_, "alta", tipo in ("ajuste", "baja", "anulacion"), txt, None, d)]
    r = _generico(ctx, col, id_, a, d)
    for e in r:
        e.importante = True
        e.resumen = "El historial de stock no debería modificarse. " + e.resumen
    return r


def _clave_item(it: dict, ctx: Contexto) -> tuple[tuple, str]:
    if it.get("turnoId"):
        return ("T", it["turnoId"]), ctx.turno_texto(it["turnoId"])
    return ("P", it.get("prodId")), ctx.producto(it.get("prodId"))


def _mapa_items(items: list | None, ctx: Contexto) -> dict:
    m: dict = {}
    for it in items or []:
        clave, nombre = _clave_item(it, ctx)
        e = m.setdefault(clave, {"nombre": nombre, "cant": 0})
        e["cant"] += it.get("cant") or 1
    return m


def _comanda(ctx, col, id_, a, d):
    lugar = ctx.lugar(id_)
    items_a = (a or {}).get("items") or []
    items_d = (d or {}).get("items") or []
    pagos_a = {p.get("id"): p for p in (a or {}).get("pagos") or []}
    pagos_d = {p.get("id"): p for p in (d or {}).get("pagos") or []}
    out: list[Entrada] = []

    ma, md = _mapa_items(items_a, ctx), _mapa_items(items_d, ctx)
    quedo_vacia = bool(items_a) and not items_d
    if quedo_vacia:
        if any(v.get("destino") == lugar or (id_ == "M" and v.get("destino") == "Mostrador") for v in ctx.ventas_nuevas):
            return [Entrada(col, id_, "cambio", False, f"Cuenta {lugar} cobrada y cerrada", None, None)]
        # ¿pasó entera a otro lugar? (mover cuenta a otra mesa / mesa suelta)
        for c2, i2, a2, d2 in ctx.cambios:
            if c2 == "comandas" and i2 != id_ and a2 is None and d2 and _mapa_items(d2.get("items"), ctx) == ma:
                return [Entrada(col, id_, "cambio", False, f"Cuenta de {lugar} movida a {ctx.lugar(i2)}", None, None)]
        detalle = ", ".join(f"{v['cant']} × {v['nombre']}" for v in ma.values())
        return [Entrada(col, id_, "baja" if d is None else "cambio", True,
                        f"Cuenta {lugar} VACIADA sin cobrar: {detalle}", {"items": items_a}, {"items": items_d})]

    for clave in list(ma) + [k for k in md if k not in ma]:
        x, y = ma.get(clave), md.get(clave)
        nombre = (y or x)["nombre"]
        ca, cd = (x or {}).get("cant", 0), (y or {}).get("cant", 0)
        if ca == cd:
            continue
        if ca == 0:
            out.append(Entrada(col, id_, "cambio", False, f"Cuenta {lugar}: se agregó {cd} × {nombre}", None, {"agregado": nombre, "cant": cd}))
        elif cd == 0:
            out.append(Entrada(col, id_, "cambio", True, f"Cuenta {lugar}: se sacó {ca} × {nombre}", {"quitado": nombre, "cant": ca}, None))
        elif cd < ca:
            out.append(Entrada(col, id_, "cambio", True, f"Cuenta {lugar}: {nombre} bajó de {ca} a {cd}", {"cant": ca}, {"cant": cd}))
        else:
            out.append(Entrada(col, id_, "cambio", False, f"Cuenta {lugar}: {nombre} subió de {ca} a {cd}", {"cant": ca}, {"cant": cd}))

    for pid, p in pagos_d.items():
        if pid not in pagos_a:
            out.append(Entrada(col, id_, "cambio", False, f"Cuenta {lugar}: pago parcial de {plata(p.get('monto'))} en {p.get('nombre')}", None, p))
    for pid, p in pagos_a.items():
        if pid not in pagos_d and d is not None and not any(v.get("destino") == lugar for v in ctx.ventas_nuevas):
            out.append(Entrada(col, id_, "cambio", True, f"Cuenta {lugar}: se devolvió el pago de {plata(p.get('monto'))} ({p.get('nombre')})", p, None))

    if a is None and not out and items_d:
        out.append(Entrada(col, id_, "alta", False, f"Cuenta {lugar} abierta", None, None))
    return out


def _turno(ctx, col, id_, a, d):
    def texto(t):
        return f"{ctx.cancha(t.get('canchaId'))} el {t.get('fecha')} a las {hm(t.get('ini'))} ({t.get('cliente') or 'sin nombre'})"

    if a is None:
        return [Entrada(col, id_, "alta", False, f"Turno agendado: {texto(d)}{' · FIJO' if d.get('esFijo') else ''}", None, d)]
    if d is None:
        return [Entrada(col, id_, "baja", True, f"TURNO BORRADO: {texto(a)}", a, None)]
    cam = _cambiados(a, d)
    if not cam:
        return []
    ant, des = _antes_despues(cam)
    if "estado" in cam and d.get("estado") == "cancelado":
        return [Entrada(col, id_, "cambio", True, f"Turno cancelado: {texto(d)}", ant, des)]
    if any(k in cam for k in ("fecha", "ini", "canchaId")):
        return [Entrada(col, id_, "cambio", True, f"Turno reprogramado: de {texto(a)} a {texto(d)}", ant, des)]
    if "estado" in cam and d.get("estado") == "cobrado":
        return [Entrada(col, id_, "cambio", False, f"Turno cobrado: {texto(d)}", ant, des)]
    solo_pagos = set(cam) <= {"pagos", "aparte", "ventaId"}
    if solo_pagos:
        return [Entrada(col, id_, "cambio", False, f"Turno {texto(d)}: se actualizaron pagos/seña", ant, des)]
    partes = [f"{CAMPOS.get(k, k)}: {ctx.valor(k, x)} → {ctx.valor(k, y)}" for k, (x, y) in cam.items()]
    return [Entrada(col, id_, "cambio", "precio" in cam, f"Turno {texto(d)}: " + "; ".join(partes), ant, des)]


def _cc(ctx, col, id_, a, d):
    nombres = {"cargo": "Fiado (cargo)", "pago": "Pago de deuda", "anulacion": "Fiado anulado"}
    if a is None:
        return [Entrada(col, id_, "alta", True, f"{nombres.get(d.get('tipo'), d.get('tipo'))} de {plata(d.get('monto'))} · {ctx.nombre('clientes', d.get('clienteId'))} {d.get('ref') or ''}".rstrip(), None, d)]
    r = _generico(ctx, col, id_, a, d)
    for e in r:
        e.importante = True
    return r


def _unico(ctx, col, id_, a, d):
    if col == "pin":
        if a == d:
            return []
        if a is None:
            return [Entrada(col, id_, "alta", False, "Código de administración inicial definido", None, None)]
        return [Entrada(col, id_, "cambio", True, "Se cambió el código de administración", None, None)]
    if col in IGNORAR_COLECCIONES:
        return []
    if a is None:
        return [Entrada(col, id_, "alta", False, f"{ETIQUETAS.get(col, col)} inicial guardado", None, d if isinstance(d, dict) else {"valor": d})]
    if isinstance(a, dict) and isinstance(d, dict):
        cam = _cambiados(a, d)
        if not cam:
            return []
        ant, des = _antes_despues(cam)
        partes = [f"{CAMPOS.get(k, k)}: {ctx.valor(k, x)} → {ctx.valor(k, y)}" for k, (x, y) in cam.items()]
        imp = any(k in IMPORTANTES.get(col, set()) for k in cam)
        return [Entrada(col, id_, "cambio", imp, f"{ETIQUETAS.get(col, col)}: " + "; ".join(partes), ant, des)]
    if a == d:
        return []
    return [Entrada(col, id_, "cambio", col == "negocio", f"{ETIQUETAS.get(col, col)}: «{a}» → «{d}»", {"valor": a}, {"valor": d})]


def _log_interno(ctx, col, id_, a, d):
    # Las líneas del registro de pantalla se guardan tal cual. Si alguien las toca, es raro.
    if a is None:
        return []
    return [Entrada(col, id_, "baja" if d is None else "cambio", True, "ATENCIÓN: se modificó o borró una línea del registro de cambios de la pantalla", a, d)]


RESUMIDORES = {
    "productos": _producto, "ventas": _venta, "caja": _libro_caja, "cajas": _caja_sesion, "movCaja": _mov_caja,
    "movimientos": _mov_stock, "comandas": _comanda, "turnos": _turno, "cc": _cc, "log": _log_interno,
}


def generar(con: sqlite3.Connection, cambios: list[tuple[str, str, dict | None, dict | None]], formas: dict[str, str]) -> list[Entrada]:
    """Convierte los cambios de un guardado en entradas del registro (en el orden en que ocurrieron)."""
    ctx = Contexto(con, cambios)
    out: list[Entrada] = []
    for col, id_, antes, despues in cambios:
        if col in IGNORAR_COLECCIONES:
            continue
        if formas.get(col) == "unico":
            ents = _unico(ctx, col, id_, antes, despues)
        else:
            ents = RESUMIDORES.get(col, _generico)(ctx, col, id_, antes, despues)
        out.extend(ents)
    return out


def serializar(e: Entrada) -> tuple[str | None, str | None]:
    return _json(e.antes), _json(e.despues)
