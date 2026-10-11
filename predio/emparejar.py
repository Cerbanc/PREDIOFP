"""Emparejador tolerante: de «COCA COLA 500CC BULTO 6X12» al producto «Coca-Cola 500 ml» de la base.

Un remito casi nunca escribe el producto igual que el negocio: abrevia, se come letras, pega los números a las unidades.
Acá se compara por palabras con tolerancia de tipeo y, sobre todo, se cuidan los NÚMEROS Y TAMAÑOS: 500 ml no es 1,5 L,
2,25 no es 2,5, «con gas» no es «sin gas». Ante la duda no se decide solo: devuelve hasta tres candidatos con puntaje y un estado.

  seguro   puntaje alto y claramente separado del segundo candidato
  revisar  puntaje medio, o hay otro candidato parecido: la persona confirma
  ninguno  nada se parece lo suficiente: la persona elige a mano

Sólo usa la biblioteca estándar. No hace pedidos a ningún lado.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

from .util import sin_acentos

UMBRAL_NINGUNO = 0.40          # debajo de esto no se sugiere nada
UMBRAL_SEGURO = 0.80           # para «seguro» hace falta superar esto...
SEPARACION_SEGURO = 0.15       # ...y llevarle al segundo al menos esta ventaja
UMBRAL_CANDIDATO = 0.25        # debajo de esto ni siquiera se muestra como opción
EXPLICADO_SEGURO = 0.70        # para «seguro», el renglón tiene que estar explicado casi entero por el producto (si trae una palabra que
                               # el producto no tiene, como «light» o «naranja», puede ser otra variante)

# Palabras que describen cómo viene o no dicen qué es: no cuentan para reconocer el producto.
RUIDO = frozenset("""
caja cajas cj cjs cja cjas caj cajon cajones bulto bultos blt bult bto btos paq paqs paquete paquetes pqt pqts pq
pack packs pk pks display dsp fardo fardos unid unidad unidades un und uni u uds unds docena doc
x de del la las el los y e en por para
""".split())
# Palabras que pueden estar o no sin que se trate de otro producto: pesan poco.
POCO_PESO = frozenset("""
pet lata latas botella botellas botellita retornable descartable ret desc vidrio tetra tetrabrik sachet envase env
sabor original clasica clasico comun nacional importado mineral cola gaseosa bebida
""".split())
PESO_POCO = 0.35

# Medidas: unidad -> (tipo, factor a ml o g)
_MEDIDAS = {
    "ml": ("v", 1), "cc": ("v", 1), "cm3": ("v", 1), "cmc": ("v", 1),
    "l": ("v", 1000), "lt": ("v", 1000), "lts": ("v", 1000), "lit": ("v", 1000), "litro": ("v", 1000), "litros": ("v", 1000),
    "g": ("p", 1), "gr": ("p", 1), "grs": ("p", 1), "gramo": ("p", 1), "gramos": ("p", 1),
    "kg": ("p", 1000), "kgs": ("p", 1000), "kilo": ("p", 1000), "kilos": ("p", 1000),
}
_NUM = r"\d+(?:[.,]\d+)?"
_UNID = "|".join(sorted(_MEDIDAS, key=len, reverse=True))
_RE_MILES = re.compile(rf"(?<![\d.,])(\d{{1,3}})\.(\d{{3}})(?=\s*(?:ml|cc|cm3|g|gr|grs|gramos)\b)")
_RE_NXM_TAM = re.compile(rf"(?<![\d.,])(\d+)\s*x\s*({_NUM})\s*({_UNID})\b")
_RE_TAM = re.compile(rf"(?<![\d.,])({_NUM})\s*({_UNID})\b")
_RE_NXM = re.compile(r"(?<![\d.,])(\d+)\s*x\s*(\d+)(?![\d.,]\d)")
_RE_X_N = re.compile(r"(?<![a-z0-9])x\s*(\d+)(?![\d.,]\d)")
_RE_N_X = re.compile(r"(?<![\d.,])(\d+)\s*x(?![a-z0-9])")
_RE_C_N = re.compile(r"(?<![a-z0-9])c\s*/\s*(\d+)")
_RE_N_U = re.compile(r"(?<![\d.,])(\d+)\s*(?:u|un|und|unid|unidad|unidades|uds|unds)(?![a-z0-9])")
_RE_SUELTO = re.compile(rf"(?<![a-z0-9.,])({_NUM})(?![a-z0-9])")
_RE_DOCENA = re.compile(r"(?<![a-z0-9])(?:docena|doc)(?![a-z0-9])")

# Escrituras distintas de lo mismo, antes de partir en palabras.
_SUSTITUCIONES = [
    (re.compile(r"(?<![a-z0-9])s\s*/\s*g(?:as)?(?![a-z0-9])|(?<![a-z0-9])sin\s+gas(?![a-z0-9])|(?<![a-z0-9])sgas(?![a-z0-9])"), " singa "),
    (re.compile(r"(?<![a-z0-9])c\s*/\s*g(?:as)?(?![a-z0-9])|(?<![a-z0-9])con\s+gas(?![a-z0-9])|(?<![a-z0-9])cgas(?![a-z0-9])|gasificad[ao]s?(?![a-z0-9])"), " conga "),
    (re.compile(r"(?<![a-z0-9])s\s*/\s*azucar(?![a-z0-9])|(?<![a-z0-9])sin\s+azucar(?![a-z0-9])|(?<![a-z0-9])zero\s+azucar(?![a-z0-9])"), " zero "),
    (re.compile(r"(?<![a-z0-9])coca[\s-]*cola(?![a-z0-9])"), " coca cola "),
    (re.compile(r"(?<![a-z0-9])(?:7\s*up|seven[\s-]*up)(?![a-z0-9])"), " seven up "),
]
# Pares que se contradicen: si uno dice una y el otro la otra, no son lo mismo.
_CONTRARIOS = (("singa", "conga"),)


def _raiz(t: str) -> str:
    """Singular simple: servilletas -> servilleta, alfajores -> alfajor, lapices -> lapiz."""
    if len(t) > 4 and t.endswith("ces"):
        return t[:-3] + "z"
    if len(t) > 5 and t.endswith(("ores", "ones", "eres")):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def _a_numero(txt: str) -> float:
    return float(txt.replace(",", "."))


@dataclass
class Analisis:
    """Un texto (del remito o el nombre de un producto) separado en palabras, medidas y cantidades por paquete."""
    palabras: list[str] = field(default_factory=list)
    medidas: set[int] = field(default_factory=set)         # tamaños en ml o g (500, 1500...)
    sueltos: list[float] = field(default_factory=list)     # números sin unidad (¿500? ¿2,25?)
    paquetes: set[int] = field(default_factory=set)        # cuántas unidades trae: x24, 6x12 -> {6, 12, 72}
    por_paquete: int | None = None                         # la cantidad principal por paquete (6x12 -> 72)


def analizar(texto) -> Analisis:
    t = sin_acentos(str(texto or "")).lower().replace("×", "x").replace("&", " y ")
    for patron, reemplazo in _SUSTITUCIONES:
        t = patron.sub(reemplazo, t)
    t = re.sub(r"(?<![a-z])x(?=\d)", "x ", t)          # x24 -> x 24
    t = _RE_MILES.sub(lambda m: m.group(1) + m.group(2), t)
    a = Analisis()

    def sacar(patron, f) -> None:
        nonlocal t
        def reemplazo(m):
            f(m)
            return " "
        t = patron.sub(reemplazo, t)

    def medida(num: str, unidad: str) -> None:
        _, factor = _MEDIDAS[unidad]
        v = int(round(_a_numero(num) * factor))
        if v > 0:
            a.medidas.add(v)

    def paquete(n: int, principal: bool = True) -> None:
        if n >= 2:
            a.paquetes.add(n)
            if principal and a.por_paquete is None:
                a.por_paquete = n

    def nxm_tam(m):
        paquete(int(m.group(1)))
        medida(m.group(2), m.group(3))

    def nxm(m):
        n, k = int(m.group(1)), int(m.group(2))
        a.paquetes.update(v for v in (n, k, n * k) if v >= 2)
        if n * k >= 2 and a.por_paquete is None:
            a.por_paquete = n * k

    sacar(_RE_NXM_TAM, nxm_tam)
    sacar(_RE_TAM, lambda m: medida(m.group(1), m.group(2)))
    sacar(_RE_NXM, nxm)
    sacar(_RE_X_N, lambda m: paquete(int(m.group(1))))
    sacar(_RE_N_X, lambda m: paquete(int(m.group(1))))
    sacar(_RE_C_N, lambda m: paquete(int(m.group(1))))
    sacar(_RE_N_U, lambda m: paquete(int(m.group(1))))
    sacar(_RE_DOCENA, lambda m: paquete(12))
    sacar(_RE_SUELTO, lambda m: a.sueltos.append(_a_numero(m.group(1))))
    for w in re.findall(r"[a-z0-9]+", t):
        if w in RUIDO or w.isdigit():
            continue
        w = _raiz(re.sub(r"(.)\1+", r"\1", w.replace("k", "c")))      # coka = coca, cooca = coca: se comparan sin letras repetidas
        if w not in RUIDO:
            a.palabras.append(w)
    return a


def _equivalentes(n: float) -> set[int]:
    """Un número suelto puede ser ml/g (500) o litros/kilos (1,5 -> 1500, 2 -> 2000)."""
    if abs(n - round(n)) > 1e-9:
        return {int(round(n * 1000))}
    k = int(round(n))
    return {k, k * 1000} if k <= 20 else {k}


def _distancia(a: str, b: str, tope: int) -> int:
    """Distancia de edición con intercambio de letras vecinas; corta apenas pasa `tope`."""
    la, lb = len(a), len(b)
    if abs(la - lb) > tope:
        return tope + 1
    prev2: list[int] | None = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            costo = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + costo)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > tope:
            return tope + 1
        prev2, prev = prev, cur
    return prev[lb]


@lru_cache(maxsize=300_000)
def parecido(a: str, b: str) -> float:
    """Qué tan parecidas son dos palabras (0 a 1). Tolera tipeos y abreviaturas, pero no confunde palabras cortas."""
    if a == b:
        return 1.0
    if a.isdigit() or b.isdigit():
        return 0.0
    corto, largo = (a, b) if len(a) <= len(b) else (b, a)
    if len(corto) < 3:
        return 0.0
    if len(corto) >= 4 and largo.startswith(corto):                    # abreviatura: salch -> salchicha
        return min(0.9, 0.7 + 0.2 * len(corto) / len(largo))
    if len(largo) < 5:                                                 # coca y cola son 1 letra distintas pero no son lo mismo
        return 0.0
    tope = 1 if len(largo) <= 7 else 2
    d = _distancia(a, b, tope)
    if d > tope:
        return 0.0
    s = 1 - 1.2 * d / len(largo)
    return s if s >= 0.6 else 0.0


def _cobertura(A: list[str], B: list[str], pa: list[float], pb: list[float]) -> tuple[float, float]:
    """Cuánto de A está explicado por B y cuánto de B por A. Cada palabra se usa una sola vez.
    También junta palabras vecinas: «cocacola» = «coca» + «cola»."""
    cand: list[tuple[float, int, tuple[int, ...], tuple[int, ...]]] = []
    for i, x in enumerate(A):
        for j, y in enumerate(B):
            s = parecido(x, y)
            if s > 0:
                cand.append((s, 0, (i,), (j,)))
    for i in range(len(A) - 1):
        junta = A[i] + A[i + 1]
        for j, y in enumerate(B):
            s = parecido(junta, y)
            if s >= 0.85:
                cand.append((s, 1, (i, i + 1), (j,)))
    for j in range(len(B) - 1):
        junta = B[j] + B[j + 1]
        for i, x in enumerate(A):
            s = parecido(x, junta)
            if s >= 0.85:
                cand.append((s, 1, (i,), (j, j + 1)))
    cand.sort(key=lambda c: (-c[0], c[1]))
    ua: dict[int, float] = {}
    ub: dict[int, float] = {}
    for s, _, ia, ib in cand:
        if any(i in ua for i in ia) or any(j in ub for j in ib):
            continue
        for i in ia:
            ua[i] = s
        for j in ib:
            ub[j] = s
    ta, tb = sum(pa), sum(pb)
    ca = sum(pa[i] * s for i, s in ua.items()) / ta if ta else 0.0
    cb = sum(pb[j] * s for j, s in ub.items()) / tb if tb else 0.0
    return ca, cb


@dataclass
class _Producto:
    id: str
    nombre: str
    analisis: Analisis
    paquete: int                      # unidades por paquete que declara el producto (1 = no declara)


class Catalogo:
    """Los productos del negocio ya analizados, para comparar muchos renglones sin repetir trabajo."""

    def __init__(self, productos: list[dict]):
        self.productos: list[_Producto] = []
        for p in productos:
            pid, nombre = str(p.get("id") or ""), str(p.get("nombre") or "").strip()
            if not pid or not nombre:
                continue
            a = analizar(nombre)
            extra = analizar(str(p.get("paqueteNombre") or ""))
            a.paquetes |= extra.paquetes
            try:
                paq = int(p.get("paqueteUnidades") or 1)
            except (TypeError, ValueError):
                paq = 1
            if paq >= 2:
                a.paquetes.add(paq)
            self.productos.append(_Producto(pid, nombre, a, max(1, paq)))
        df: dict[str, int] = {}
        for p in self.productos:
            for w in set(p.analisis.palabras):
                df[w] = df.get(w, 0) + 1
        self._df, self._n = df, len(self.productos)

    def peso(self, palabra: str) -> float:
        if palabra in POCO_PESO:
            return PESO_POCO
        df = self._df.get(palabra, 0)
        return 1.0 + min(0.8, 0.3 * math.log((self._n + 1) / (df + 1)))

    def puntaje(self, t: Analisis, p: _Producto) -> float:
        """0 a 1: qué tan probable es que el renglón `t` sea el producto `p`."""
        return self._evaluar(t, p)[0]

    def _evaluar(self, t: Analisis, p: _Producto) -> tuple[float, float]:
        """-> (puntaje, qué parte del renglón explica el producto)"""
        if not t.palabras or not p.analisis.palabras:
            return 0.0, 0.0
        a = p.analisis
        cp, ct = _cobertura(a.palabras, t.palabras, [self.peso(w) for w in a.palabras], [self.peso(w) for w in t.palabras])
        base = cp * (0.5 + 0.5 * ct)     # lo que el producto tiene y el renglón no pesa más que lo que el renglón agrega
        if base <= 0:
            return 0.0, ct
        for x, y in _CONTRARIOS:
            if (x in t.palabras and y in a.palabras) or (y in t.palabras and x in a.palabras):
                base *= 0.5
        factor, coincide = self._numeros(t, a)
        if coincide:
            base += (1 - base) * 0.12
        base *= factor
        if t.paquetes & a.paquetes:
            base = min(1.0, base + 0.04)
        return max(0.0, min(1.0, base)), ct

    @staticmethod
    def _numeros(t: Analisis, p: Analisis) -> tuple[float, bool]:
        """Los tamaños pesan mucho: si los dos dicen un tamaño y no coinciden, casi seguro es otro producto."""
        hay_t, hay_p = bool(t.medidas or t.sueltos), bool(p.medidas or p.sueltos)
        if not hay_t:
            return (0.93 if hay_p else 1.0), False
        if not hay_p:
            return 0.9, False
        vt = set(t.medidas)
        for n in t.sueltos:
            vt |= _equivalentes(n)
        vp = set(p.medidas)
        for n in p.sueltos:
            vp |= _equivalentes(n)
        if vt & vp or set(t.sueltos) & set(p.sueltos):
            return 1.0, True
        return 0.3, False

    def buscar(self, texto: str, maximo: int = 3) -> dict:
        """-> {candidatos: [{id, nombre, puntaje}], estado: seguro|revisar|ninguno, explicado: qué parte del renglón explica el primero}"""
        t = analizar(texto)
        puntos = sorted(((*self._evaluar(t, p), p) for p in self.productos), key=lambda x: (-x[0], x[2].nombre))
        cands = [{"id": p.id, "nombre": p.nombre, "puntaje": round(s, 3)} for s, _, p in puntos[:maximo] if s >= UMBRAL_CANDIDATO]
        if not cands or cands[0]["puntaje"] < UMBRAL_NINGUNO:
            return {"candidatos": cands, "estado": "ninguno", "explicado": 0.0}
        primero = cands[0]["puntaje"]
        segundo = cands[1]["puntaje"] if len(cands) > 1 else 0.0
        seguro = primero >= UMBRAL_SEGURO and primero - segundo >= SEPARACION_SEGURO and puntos[0][1] >= EXPLICADO_SEGURO
        return {"candidatos": cands, "estado": "seguro" if seguro else "revisar", "explicado": round(puntos[0][1], 3)}

    def puntaje_de(self, texto: str, producto_id: str) -> float:
        """Puntaje de un producto puntual (sirve para evaluar la elección del modelo)."""
        t = analizar(texto)
        for p in self.productos:
            if p.id == producto_id:
                return self.puntaje(t, p)
        return 0.0

    def nombre(self, producto_id: str) -> str | None:
        for p in self.productos:
            if p.id == producto_id:
                return p.nombre
        return None

    def paquete_de(self, producto_id: str) -> int:
        for p in self.productos:
            if p.id == producto_id:
                return p.paquete
        return 1


def emparejar(texto: str, productos: list[dict], maximo: int = 3) -> dict:
    """Atajo para un solo renglón (para varios conviene armar un Catalogo una vez)."""
    return Catalogo(productos).buscar(texto, maximo)
