"""Lectura de remitos con foto: emparejador tolerante, cliente de Gemini (con un servidor falso), endpoint y combinación IA + local."""
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import base_inicial, op
from fake_gemini import CLAVE_DE_PRUEBA, GeminiFalso, error_gemini, respuesta_gemini
from predio import app as app_mod
from predio import remito
from predio.config import AJUSTES_POR_DEFECTO, leer_ajustes
from predio.emparejar import Catalogo, analizar
from predio.servidor import Servidor

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a2e6b2a00000000049454e44ae426082")


def P(id_, nombre, paq=1, paqnombre="", tipo="directo", activo=True):
    return {"id": id_, "nombre": nombre, "paqueteUnidades": paq, "paqueteNombre": paqnombre, "tipo": tipo, "activo": activo}


# Un negocio de verdad: con tamaños parecidos, marcas parecidas y variantes que NO hay que confundir.
CATALOGO = [
    P("coca500", "Coca-Cola 500 ml", 72, "Bulto 6 × 12"), P("coca15", "Coca-Cola 1,5 L", 6, "Pack × 6"), P("coca225", "Coca-Cola 2,25 L", 6),
    P("cocaz", "Coca-Cola Zero 500 ml", 12), P("sprite", "Sprite 500 ml", 12), P("sprite225", "Sprite 2,25 L", 6),
    P("seven", "Seven Up 500 ml", 12), P("fanta", "Fanta 500 ml", 12), P("pepsi", "Pepsi 500 ml", 12), P("pepsi225", "Pepsi 2,25 L", 6),
    P("aguasg", "Agua sin gas 500 ml", 6, "Pack × 6"), P("aguacg", "Agua con gas 500 ml", 6), P("agua15", "Agua sin gas 1,5 L", 6),
    P("gato", "Gatorade 500 ml", 12), P("alfa", "Alfajor", 24, "Caja × 24"), P("alfat", "Alfajor triple", 24, "Caja × 24"),
    P("papas", "Papas fritas", 12), P("lays", "Papas Lays 150 g"), P("chicles", "Chicles", 20),
    P("salch", "Salchicha", 6, "Paquete × 6", "insumo"), P("pan", "Pan de pancho", 6, "Paquete × 6", "insumo"),
    P("panh", "Pan de hamburguesa", 6, "Paquete × 6", "insumo"), P("medal", "Medallón de carne", 10, "", "insumo"),
    P("queso", "Feta de queso", 20, "", "insumo"), P("cerv", "Cerveza 1 L", 12, "Cajón × 12"),
    P("pelotas", "Pelotas de pádel (tubo × 3)", 24, "Caja × 24"), P("grip", "Grip", 12), P("hielo", "Bolsa de hielo 3 kg"),
]
CAT = Catalogo(CATALOGO)


# ------------------------------------------------------------------------------------------------ análisis de textos
@pytest.mark.parametrize("texto, medidas, paquetes, por_paquete", [
    ("COCA COLA 500CC BULTO 6X12", {500}, {6, 12, 72}, 72),
    ("Coca-Cola 500 ml", {500}, set(), None),
    ("COCA COLA 1,5 LT", {1500}, set(), None),
    ("coca cola 2.25 l", {2250}, set(), None),
    ("GASEOSA 1.500 CC", {1500}, set(), None),
    ("AGUA 1500cc x 6", {1500}, {6}, 6),
    ("ALFAJOR TRIPLE CJ X24", set(), {24}, 24),
    ("ALFAJOR X 24 UN", set(), {24}, 24),
    ("SALCHICHA VIENA PAQ X6", set(), {6}, 6),
    ("SERVILLETAS PAPEL X500", set(), {500}, 500),
    ("YERBA 500G X 10", {500}, {10}, 10),
    ("PAPAS LAYS 150 GRS", {150}, set(), None),
    ("2X500ML", {500}, {2}, 2),
    ("HIELO 3KG", {3000}, set(), None),
    ("Pelotas de pádel (tubo × 3)", set(), {3}, 3),
])
def test_analizar_medidas_y_paquetes(texto, medidas, paquetes, por_paquete):
    a = analizar(texto)
    assert a.medidas == medidas and a.paquetes == paquetes and a.por_paquete == por_paquete, (texto, a)


def test_analizar_palabras_sin_ruido_y_en_singular():
    assert analizar("ALFAJORES TRIPLES CJ X24").palabras == ["alfajor", "triple"]
    assert analizar("COCA COLA 500CC BULTO 6X12").palabras == ["coca", "cola"]
    assert analizar("Coca-Cola 500 ml").palabras == ["coca", "cola"]
    assert analizar("Cocacola 500").palabras == ["coca", "cola"]
    assert analizar("PAN PANCHO X6").palabras == ["pan", "pancho"]
    assert analizar("Agua S/GAS 500").palabras == ["agua", "singa"]
    assert analizar("AGUA CON GAS").palabras == ["agua", "conga"]
    assert analizar("Pan de hamburguesa").palabras == ["pan", "hamburguesa"]
    assert analizar("7UP").palabras == ["seven", "up"]
    assert analizar("") .palabras == [] and analizar(None).palabras == []


# ------------------------------------------------------------------------------------------------ emparejador: casos reales
# (texto del remito, producto esperado, estados aceptables)
CASOS = [
    ("COCA COLA 500CC BULTO 6X12", "coca500", {"seguro"}),
    ("SALCHICHA VIENA PAQ X6", "salch", {"seguro", "revisar"}),
    ("PAN PANCHO X6", "pan", {"seguro"}),
    ("ALFAJOR TRIPLE CJ X24", "alfat", {"seguro"}),
    ("Cocacola 500", "coca500", {"seguro", "revisar"}),
    ("Cooca cola 500 cc", "coca500", {"seguro", "revisar"}),
    ("COKA COLA 500", "coca500", {"seguro", "revisar"}),
    ("Coca cola 500ml retornable", "coca500", {"seguro", "revisar"}),
    ("Alfajor triple", "alfat", {"seguro"}),
    ("Alfajores triples x24", "alfat", {"seguro"}),
    ("ALFAJOR", "alfa", {"seguro"}),
    ("Salchichas", "salch", {"seguro"}),
    ("Pan de panchos", "pan", {"seguro"}),
    ("Pan hamburguesa x6", "panh", {"seguro"}),
    ("Medallon de carne", "medal", {"seguro"}),
    ("Coca 500", "coca500", {"seguro", "revisar"}),
    ("COCA COLA 1.5 LT", "coca15", {"seguro"}),
    ("COCA COLA 1500CC", "coca15", {"seguro"}),
    ("COCA COLA 2,25", "coca225", {"seguro"}),
    ("COCA COLA 2.25 L", "coca225", {"seguro"}),
    ("COCA COLA ZERO 500", "cocaz", {"seguro"}),
    ("COCA COLA SIN AZUCAR 500CC", "cocaz", {"seguro"}),
    ("SPRITE 500", "sprite", {"seguro"}),
    ("SEVEN UP 500CC", "seven", {"seguro"}),
    ("7UP 500", "seven", {"seguro"}),
    ("AGUA S/GAS 500", "aguasg", {"seguro"}),
    ("AGUA C/GAS 500CC", "aguacg", {"seguro"}),
    ("AGUA CON GAS 500", "aguacg", {"seguro"}),
    ("AGUA SIN GAS 1.5L", "agua15", {"seguro"}),
    ("GASEOSA PEPSI 2.25", "pepsi225", {"seguro", "revisar"}),
    ("CERVEZA 1LT", "cerv", {"seguro"}),
    ("PELOTAS DE PADEL TUBO X3 CJ X24", "pelotas", {"seguro"}),
    ("PAPAS LAYS 150G", "lays", {"seguro"}),
    ("PAPAS FRITAS X12", "papas", {"seguro"}),
    ("Gatorade", "gato", {"seguro", "revisar"}),
    ("Hielo 3kg", "hielo", {"revisar", "seguro"}),
]


@pytest.mark.parametrize("texto, esperado, estados", CASOS)
def test_emparejar_casos_reales(texto, esperado, estados):
    r = CAT.buscar(texto)
    assert r["candidatos"] and r["candidatos"][0]["id"] == esperado, (texto, r)
    assert r["estado"] in estados, (texto, r)
    assert len(r["candidatos"]) <= 3
    puntos = [c["puntaje"] for c in r["candidatos"]]
    assert puntos == sorted(puntos, reverse=True) and all(0 <= x <= 1 for x in puntos)


@pytest.mark.parametrize("texto", ["SERVILLETAS PAPEL X500", "ENCENDEDOR BIC", "BOLSAS DE RESIDUOS 60X90", "", "   ", "12345", "X24"])
def test_lo_que_no_esta_queda_en_ninguno(texto):
    r = CAT.buscar(texto)
    assert r["estado"] == "ninguno", (texto, r)


@pytest.mark.parametrize("texto, no_debe_ser", [
    ("COCA COLA 2.5 L", {"coca225", "coca15", "coca500"}),          # 2,25 y 2,5 no son lo mismo
    ("Coca Cola 3 L", {"coca225", "coca15", "coca500"}),
    ("COCA COLA 330", {"coca500", "coca15", "coca225"}),
    ("SPRITE 1 L", {"sprite", "sprite225"}),
    ("PEPSI 1.5", {"pepsi", "pepsi225"}),
    ("CERVEZA 330 ML", {"cerv"}),
])
def test_un_tamano_distinto_nunca_se_da_por_bueno(texto, no_debe_ser):
    r = CAT.buscar(texto)
    sugeridos = {c["id"] for c in r["candidatos"] if c["puntaje"] >= 0.4}
    assert r["estado"] == "ninguno" and not (sugeridos & no_debe_ser), (texto, r)


def test_el_tamano_decide_entre_productos_parecidos():
    def top(t):
        return CAT.buscar(t)["candidatos"][0]["id"]
    assert top("COCA COLA 500") == "coca500" and top("COCA COLA 1500") == "coca15" and top("COCA COLA 2250") == "coca225"
    assert top("PEPSI 500 CC") == "pepsi" and top("PEPSI 2,25 LTS") == "pepsi225"
    assert top("SPRITE 500CC") == "sprite" and top("SPRITE 2.25") == "sprite225"
    assert top("AGUA 1,5") == "agua15"


def test_sprite_y_seven_up_no_se_confunden():
    for texto, bien, mal in [("SPRITE 500", "sprite", "seven"), ("SEVEN UP 500", "seven", "sprite"), ("Sprite 500cc", "sprite", "seven"), ("7 up 500", "seven", "sprite")]:
        r = CAT.buscar(texto)
        assert r["candidatos"][0]["id"] == bien
        assert mal not in {c["id"] for c in r["candidatos"] if c["puntaje"] >= 0.5}, (texto, r)
    solo = CAT.buscar("Sprite")      # sin tamaño: dos Sprite posibles, pero nunca el Seven Up
    assert solo["estado"] == "revisar" and {c["id"] for c in solo["candidatos"][:2]} == {"sprite", "sprite225"}


def test_con_gas_y_sin_gas_no_se_confunden():
    r = CAT.buscar("AGUA S/GAS 500")
    assert r["candidatos"][0]["id"] == "aguasg" and r["estado"] == "seguro"
    assert all(c["puntaje"] < 0.4 for c in r["candidatos"] if c["id"] == "aguacg")
    r = CAT.buscar("AGUA CON GAS 500")
    assert r["candidatos"][0]["id"] == "aguacg" and all(c["puntaje"] < 0.4 for c in r["candidatos"] if c["id"] == "aguasg")
    # sin decir si tiene gas o no, la duda es de la persona: nunca seguro
    r = CAT.buscar("AGUA 500")
    assert r["estado"] == "revisar" and {c["id"] for c in r["candidatos"][:2]} == {"aguasg", "aguacg"}


def test_sin_tamano_en_el_renglon_con_varios_tamanos_pide_revisar():
    r = CAT.buscar("COCA COLA")
    assert r["estado"] == "revisar" and len(r["candidatos"]) == 3
    assert {c["id"] for c in r["candidatos"]} == {"coca500", "coca15", "coca225"}


def test_palabra_de_mas_en_el_renglon_no_da_seguro():
    # «light», «naranja»: el producto de la lista no la tiene, puede ser otra variante
    assert CAT.buscar("COCA COLA LIGHT 500")["estado"] == "revisar"
    assert CAT.buscar("GATORADE NARANJA 500ML")["estado"] == "revisar"
    assert CAT.buscar("FANTA UVA 500")["estado"] == "revisar"


def test_dos_productos_iguales_quedan_en_revisar():
    cat = Catalogo([P("a", "Alfajor Jorgito"), P("b", "Alfajor Jorgito")])
    r = cat.buscar("ALFAJOR JORGITO")
    assert r["estado"] == "revisar" and {c["id"] for c in r["candidatos"]} == {"a", "b"}


def test_cantidad_por_paquete_del_producto_suma_puntos():
    cat = Catalogo([P("a", "Galletitas dulces 3 unidades"), P("b", "Galletitas dulces", 24)])
    r = cat.buscar("GALLETITAS DULCES X24")
    assert r["candidatos"][0]["id"] == "b"


def test_catalogo_tolera_datos_raros():
    cat = Catalogo([{"id": "a", "nombre": "Alfajor", "paqueteUnidades": "24"}, {"id": "b", "nombre": None}, {"nombre": "sin id"},
                    {"id": "c", "nombre": "Chicle", "paqueteUnidades": None}, {"id": "d", "nombre": "Mate", "paqueteUnidades": "mucho"}])
    assert cat.buscar("alfajor")["candidatos"][0]["id"] == "a"
    assert cat.buscar("chicle")["estado"] == "seguro" and cat.buscar("mate")["estado"] == "seguro"
    assert Catalogo([]).buscar("lo que sea") == {"candidatos": [], "estado": "ninguno", "explicado": 0.0}


def test_emparejar_con_muchos_productos_es_rapido():
    grande = [P(f"x{i}", f"Producto {i} marca {i % 37} sabor {i % 11} {500 + i % 7 * 100} ml") for i in range(800)]
    t = time.time()
    cat = Catalogo(grande)
    for texto, _, _ in CASOS:
        cat.buscar(texto)
    assert time.time() - t < 5


# ------------------------------------------------------------------------------------------------ cliente de Gemini
@pytest.fixture()
def gemini(monkeypatch):
    g = GeminiFalso().iniciar()
    monkeypatch.setenv("PREDIO_API_GEMINI", g.url)
    monkeypatch.setattr(remito, "PAUSA_REINTENTO", 0)
    yield g
    g.parar()


def item(texto, cantidad=1, presentacion="unidad", por=None, producto_id=None, confianza=0.0, dudoso=False):
    return {"texto": texto, "cantidad": cantidad, "presentacion": presentacion, "unidades_por_presentacion": por,
            "producto_id": producto_id, "confianza": confianza, "dudoso": dudoso}


def ajustes(**kw):
    return {**AJUSTES_POR_DEFECTO, "gemini_clave": CLAVE_DE_PRUEBA, **kw}


def leer(gemini, items, productos=CATALOGO, **kw):
    gemini.items = items
    return remito.leer_remito(PNG, "image/png", productos, ajustes(**kw))


def test_el_pedido_a_gemini_tiene_la_forma_pedida(gemini):
    res = leer(gemini, [item("COCA COLA 500CC BULTO 6X12", 2, "bulto", 72, "coca500", 0.95)])
    assert len(gemini.pedidos) == 1
    p = gemini.ultimo()
    assert p["ruta"] == "/v1beta/models/gemini-flash-latest:generateContent"
    assert p["cabeceras"]["x-goog-api-key"] == CLAVE_DE_PRUEBA
    assert CLAVE_DE_PRUEBA not in p["ruta"] and CLAVE_DE_PRUEBA not in json.dumps(p["cuerpo"]), "la clave va sólo en el encabezado"
    cfg = p["cuerpo"]["generationConfig"]
    assert cfg["temperature"] == 0 and cfg["responseMimeType"] == "application/json"
    esquema = cfg["responseSchema"]["properties"]["items"]["items"]["properties"]
    assert set(esquema) == {"texto", "cantidad", "presentacion", "unidades_por_presentacion", "producto_id", "confianza", "dudoso"}
    assert esquema["presentacion"]["enum"] == ["unidad", "caja", "bulto", "paquete", "pack", "otro"]
    partes = p["cuerpo"]["contents"][0]["parts"]
    assert partes[0]["text"].startswith("Sos un asistente que lee remitos") and partes[1]["inline_data"]["mime_type"] == "image/png"
    assert gemini.imagen_recibida() == PNG
    # el prompt lleva la lista de productos del negocio (id | nombre | cómo viene)
    prompt = gemini.prompt_recibido()
    assert "coca500 | Coca-Cola 500 ml | Bulto 6 × 12 (72 u.)" in prompt and "hielo | Bolsa de hielo 3 kg |" in prompt
    assert res["huella"] == hashlib.sha256(PNG).hexdigest() and res["proveedor"] == "gemini" and res["modelo"] == "gemini-flash-latest"


def test_el_prompt_pide_lo_que_corresponde():
    for frase in ("SOLO con JSON", "NO inventes renglones", "coma decimal", "IGNORÁ totales", "unidades_por_presentacion", "producto_id",
                  "Si no estás seguro, devolvé null", "confianza", "dudoso", "500 ml no es 1,5 L", "con gas o sin gas"):
        assert frase in remito.PROMPT, frase
    assert "Argentina" in remito.PROMPT and "kiosco" in remito.PROMPT


def test_el_prompt_solo_lista_productos_elegibles_y_con_tope():
    elegibles = remito.productos_elegibles([P("a", "Coca"), P("b", "Pancho", tipo="receta"), P("c", "Turno", tipo="servicio"), P("d", "Pan", tipo="insumo"),
                                            P("e", "Viejo", activo=False)])
    assert [p["id"] for p in elegibles] == ["a", "d"]
    muchos = [P(f"p{i}", f"Producto {i}") for i in range(remito.MAX_PRODUCTOS_EN_PROMPT + 50)]
    assert remito.lista_de_productos(muchos).count("\n") + 1 == remito.MAX_PRODUCTOS_EN_PROMPT


def test_modelo_configurable(gemini):
    leer(gemini, [], gemini_modelo="gemini-2.5-flash")
    assert gemini.ultimo()["ruta"].endswith("/models/gemini-2.5-flash:generateContent")
    leer(gemini, [], gemini_modelo="models/gemini-otro")
    assert gemini.ultimo()["ruta"].endswith("/models/gemini-otro:generateContent")
    with pytest.raises(remito.ErrorRemito) as e:
        leer(gemini, [], gemini_modelo="../../x?y=1")
    assert e.value.codigo == "modelo"


def test_la_url_base_solo_acepta_https_o_esta_pc(monkeypatch):
    monkeypatch.setenv("PREDIO_API_GEMINI", "http://evil.example.com/v1beta")
    assert remito.Gemini.base() == remito.API_POR_DEFECTO
    monkeypatch.setenv("PREDIO_API_GEMINI", "https://proxy.interno/v1beta/")
    assert remito.Gemini.base() == "https://proxy.interno/v1beta"
    monkeypatch.delenv("PREDIO_API_GEMINI")
    assert remito.Gemini.base() == "https://generativelanguage.googleapis.com/v1beta"


def test_renglones_emparejados_con_la_eleccion_de_la_ia(gemini):
    res = leer(gemini, [
        item("COCA COLA 500CC BULTO 6X12", 2, "bulto", 72, "coca500", 0.95),
        item("PAN PANCHO X6", 3, "paquete", 6, "pan", 0.9),
        item("SERVILLETAS PAPEL X500", 2, "paquete", 500, None, 0.0),
        item("ALFAJOR TRIPLE CJ X24", 1, "caja", None, "alfat", 0.9),
    ])
    coca, pan, serv, alfa = res["lineas"]
    assert (coca["sugerido_id"], coca["estado"], coca["unidades"], coca["unidades_por_presentacion"]) == ("coca500", "seguro", 144, 72)
    assert (pan["sugerido_id"], pan["estado"], pan["unidades"]) == ("pan", "seguro", 18)
    assert (serv["sugerido_id"], serv["estado"], serv["unidades"]) == (None, "ninguno", 1000)
    assert (alfa["sugerido_id"], alfa["unidades"], alfa["unidades_por_presentacion"]) == ("alfat", 24, 24)      # «X24» del propio renglón
    for l in res["lineas"]:
        assert set(l) >= {"texto", "cantidad", "presentacion", "unidades_por_presentacion", "candidatos", "estado", "sugerido_id", "unidades"}
        assert all(set(c) == {"id", "nombre", "puntaje"} for c in l["candidatos"])


def test_si_la_ia_y_el_local_no_coinciden_queda_en_revisar(gemini):
    res = leer(gemini, [item("COCA COLA 500CC", 1, "unidad", None, "coca15", 0.95)])
    l = res["lineas"][0]
    assert l["estado"] == "revisar", l
    assert l["sugerido_id"] == "coca500", "el texto dice 500 y la elección de la IA es de 1,5 L: se sugiere lo que dice el texto"
    assert {"coca500", "coca15"} <= {c["id"] for c in l["candidatos"]}


def test_la_ia_sola_no_alcanza_para_dar_algo_por_seguro(gemini):
    # el nombre del remito no se parece a nada de la lista; la IA, que conoce las marcas, propone uno
    productos = [P("coca225", "Coca-Cola 2,25 L", 6), P("agua", "Agua sin gas 500 ml")]
    res = leer(gemini, [item("GASEOSA COLA GDE", 1, "unidad", None, "coca225", 0.9)], productos)
    l = res["lineas"][0]
    assert l["sugerido_id"] == "coca225" and l["estado"] == "revisar"
    res = leer(gemini, [item("GASEOSA COLA GDE", 1, "unidad", None, "coca225", 0.3)], productos)
    assert res["lineas"][0]["estado"] == "ninguno" and res["lineas"][0]["sugerido_id"] is None, "con poca confianza no se sugiere"


def test_si_la_ia_no_se_anima_o_duda_no_hay_seguro_salvo_parecido_casi_exacto(gemini):
    res = leer(gemini, [item("Gatorade", 1), item("Medallon de carne", 1), item("PAN PANCHO X6", 1, "paquete", 6, "pan", 0.9, dudoso=True)])
    gato, medal, pan = res["lineas"]
    assert gato["sugerido_id"] == "gato" and gato["estado"] == "seguro"                                        # parecido casi exacto (.93): alcanza sin la IA
    assert medal["sugerido_id"] == "medal" and medal["estado"] == "seguro"                                    # exacto
    assert pan["sugerido_id"] == "pan" and pan["estado"] == "revisar", "si la IA marcó dudoso, la persona revisa"


def test_un_id_inventado_por_la_ia_se_ignora(gemini):
    res = leer(gemini, [item("PAN PANCHO X6", 1, "paquete", 6, "p-que-no-existe", 0.99), item("Grip", 2, "unidad", None, "p2", 0.9)])
    pan, grip = res["lineas"]
    assert pan["sugerido_id"] == "pan" and "p-que-no-existe" not in json.dumps(pan)
    assert grip["sugerido_id"] == "grip", "p2 no es un producto elegible (en esta lista no existe)"


def test_unidades_segun_presentacion(gemini):
    res = leer(gemini, [
        item("SALCHICHA VIENA", 4, "paquete", None, "salch", 0.9),             # el producto sabe que trae 6
        item("CHICLES", 3, "caja", None, "chicles", 0.9),                      # el producto sabe que trae 20
        item("GRIP", 5, "unidad", None, "grip", 0.9),
        item("HIELO", 2, "bulto", None, "hielo", 0.9),                        # nadie dice cuántas trae
        item("CERVEZA 1 LT", 2, "caja", 12, "cerv", 0.9),
        item("PAPAS FRITAS", 1, "otro", 8, "papas", 0.9),                      # remito (8) y producto (12) no coinciden
    ])
    salch, chic, grip, hielo, cerv, papas = res["lineas"]
    assert (salch["unidades"], salch["unidades_por_presentacion"], salch["por_fuente"]) == (24, 6, "producto")
    assert (chic["unidades"], chic["por_fuente"]) == (60, "producto")
    assert (grip["unidades"], grip["unidades_por_presentacion"]) == (5, None)
    assert hielo["unidades_por_presentacion"] is None and hielo["unidades"] == 2 and hielo["estado"] != "seguro" and hielo["avisos"]
    assert (cerv["unidades"], cerv["por_fuente"], cerv["estado"]) == (24, "remito", "seguro")
    assert papas["unidades"] == 8 and papas["estado"] == "revisar" and any("12" in a for a in papas["avisos"])


def test_x24_en_un_renglon_por_unidad_avisa(gemini):
    res = leer(gemini, [item("ALFAJOR TRIPLE X24", 2, "unidad", None, "alfat", 0.95)])
    l = res["lineas"][0]
    assert l["unidades"] == 2 and l["estado"] == "revisar" and any("x24" in a for a in l["avisos"])


def test_datos_raros_de_la_ia_se_limpian(gemini):
    crudos = [
        {"texto": "  GRIP \n NEGRO ", "cantidad": "3,5", "presentacion": "CAJA", "unidades_por_presentacion": "12", "producto_id": "grip", "confianza": "0,9", "dudoso": 0},
        {"texto": "", "cantidad": 1}, {"cantidad": 3}, "no soy un dict", 7,
        {"texto": "COCA", "cantidad": -2, "presentacion": "quién sabe", "unidades_por_presentacion": 0, "producto_id": 123, "confianza": 5, "dudoso": False},
        {"texto": "PAN", "cantidad": None, "presentacion": None, "unidades_por_presentacion": "x", "producto_id": "", "confianza": "nada"},
        {"texto": "MATE", "cantidad": float("inf"), "presentacion": "unidad"},
    ]
    limpios = remito.normalizar_renglones([c for c in crudos if isinstance(c, dict)])
    assert [l["texto"] for l in limpios] == ["GRIP NEGRO", "COCA", "PAN", "MATE"]
    grip, coca, pan, mate = limpios
    assert (grip["cantidad"], grip["presentacion"], grip["por"], grip["confianza"], grip["dudoso"]) == (3.5, "caja", 12, 0.9, False)
    assert (coca["cantidad"], coca["dudoso"], coca["presentacion"], coca["por"], coca["producto_id"], coca["confianza"]) == (0.0, True, "otro", None, "123", 1.0)
    assert (pan["cantidad"], pan["dudoso"], pan["presentacion"], pan["producto_id"], pan["confianza"]) == (0.0, True, "unidad", None, 0.0)
    assert mate["cantidad"] == 0.0 and mate["dudoso"] is True
    assert len(remito.normalizar_renglones([{"texto": f"x{i}", "cantidad": 1} for i in range(1000)])) == remito.MAX_RENGLONES
    assert remito._numero("1.500") == 1500 and remito._numero("1,5") == 1.5 and remito._numero(True) is None and remito._numero("abc") is None


def test_json_con_comillas_de_codigo_o_lista_suelta(gemini):
    assert remito.interpretar_json('```json\n[{"texto": "A", "cantidad": 1}]\n```') == [{"texto": "A", "cantidad": 1}]
    assert remito.interpretar_json('{"items": [{"texto": "A"}]}') == [{"texto": "A"}]
    assert remito.interpretar_json('{"renglones": [{"texto": "A"}, 5]}') == [{"texto": "A"}]
    for roto in ("", "no es json", '{"otra": 1}', "[1, 2", "null"):
        with pytest.raises(remito.ErrorRemito) as e:
            remito.interpretar_json(roto)
        assert e.value.codigo == "ilegible"


# ------------------------------------------------------------------------------------------------ errores de Google
def leer_con_error(gemini, estado, cuerpo, **kw):
    gemini.forzar(estado, cuerpo)
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, ajustes(**kw))
    return e.value


def test_errores_de_google_en_castellano(gemini):
    e = leer_con_error(gemini, 400, error_gemini(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."))
    assert e.codigo == "clave_invalida" and "no aceptó la clave" in e.mensaje
    e = leer_con_error(gemini, 403, error_gemini(403, "PERMISSION_DENIED", "Method doesn't allow unregistered callers"))
    assert e.codigo == "clave_invalida"
    e = leer_con_error(gemini, 429, error_gemini(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota",
                       [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]))
    assert e.codigo == "cupo_minuto" and e.estado == 429 and "Esperá un minuto y probá de nuevo" in e.mensaje
    e = leer_con_error(gemini, 429, error_gemini(429, "RESOURCE_EXHAUSTED", "Quota exceeded",
                       [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]))
    assert e.codigo == "cupo_dia" and "límite gratis de hoy" in e.mensaje
    e = leer_con_error(gemini, 429, error_gemini(429, "RESOURCE_EXHAUSTED", "quota", [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3600s"}]))
    assert e.codigo == "cupo_dia"
    e = leer_con_error(gemini, 429, "no es json")
    assert e.codigo == "cupo_minuto", "ante la duda se pide esperar un minuto"
    e = leer_con_error(gemini, 404, error_gemini(404, "NOT_FOUND", "models/xx is not found"), gemini_modelo="xx")
    assert e.codigo == "modelo" and "«xx»" in e.mensaje
    e = leer_con_error(gemini, 400, error_gemini(400, "INVALID_ARGUMENT", "Request payload size exceeds the limit: 20971520 bytes."))
    assert e.codigo == "imagen_grande" and e.estado == 413
    e = leer_con_error(gemini, 413, "")
    assert e.codigo == "imagen_grande"
    e = leer_con_error(gemini, 400, error_gemini(400, "FAILED_PRECONDITION", "User location is not supported for the API use."))
    assert e.codigo == "ubicacion"
    e = leer_con_error(gemini, 418, error_gemini(418, "TEAPOT", "soy una tetera"))
    assert e.codigo == "rechazada" and "418" in e.mensaje and "tetera" not in e.mensaje


def test_si_google_esta_saturado_se_repite_una_vez(gemini):
    gemini.forzar(503, error_gemini(503, "UNAVAILABLE", "The model is overloaded."))
    res = leer(gemini, [item("GRIP", 1, "unidad", None, "grip", 0.9)])
    assert len(gemini.pedidos) == 2 and res["lineas"][0]["sugerido_id"] == "grip"
    gemini.pedidos.clear()
    gemini.forzar(500, error_gemini(500, "INTERNAL", "Internal error"))
    gemini.forzar(500, error_gemini(500, "INTERNAL", "Internal error"))
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, ajustes())
    assert e.value.codigo == "servicio" and "Google no está respondiendo bien" in e.value.mensaje and len(gemini.pedidos) == 2


def test_respuestas_ilegibles_o_bloqueadas(gemini):
    e = leer_con_error(gemini, 200, {"candidates": [{"content": {"parts": [{"text": "esto no es json"}]}}]})
    assert e.codigo == "ilegible" and "no se pudo leer" in e.mensaje
    e = leer_con_error(gemini, 200, "{ roto")
    assert e.codigo == "ilegible"
    e = leer_con_error(gemini, 200, {"candidates": []})
    assert e.codigo == "ilegible"
    e = leer_con_error(gemini, 200, {"promptFeedback": {"blockReason": "SAFETY"}})
    assert e.codigo == "bloqueada"
    e = leer_con_error(gemini, 200, {"candidates": [{"content": {"parts": []}}]})
    assert e.codigo == "ilegible"
    e = leer_con_error(gemini, 200, {"candidates": [{"content": {"parts": [{"text": "pensando...", "thought": True}]}}]})
    assert e.codigo == "ilegible", "el texto de 'pensamiento' del modelo no cuenta como respuesta"


def test_sin_internet_y_demasiado_lenta(gemini, monkeypatch):
    monkeypatch.setattr(remito, "TIMEOUT", 0.3)
    gemini.forzar(200, respuesta_gemini([]), demora=1.5)
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, ajustes())
    assert e.value.codigo == "timeout" and "tardó demasiado" in e.value.mensaje
    gemini.parar()
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, ajustes())
    assert e.value.codigo == "sin_internet" and "No hay internet" in e.value.mensaje


def test_sin_clave_y_clave_con_espacios(gemini):
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, dict(AJUSTES_POR_DEFECTO))
    assert e.value.codigo == "sin_clave" and not gemini.pedidos
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, ajustes(gemini_clave="AIza con espacio"))
    assert e.value.codigo == "clave_invalida" and not gemini.pedidos
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, ajustes(gemini_clave="AIzañ"))
    assert e.value.codigo == "clave_invalida" and not gemini.pedidos
    gemini.items = []
    remito.leer_remito(PNG, "image/png", CATALOGO, ajustes(gemini_clave=f'  "{CLAVE_DE_PRUEBA}"  '))      # comillas y espacios de más al pegar
    assert gemini.pedidos[-1]["cabeceras"]["x-goog-api-key"] == CLAVE_DE_PRUEBA


def test_la_clave_equivocada_da_clave_invalida(gemini):
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, ajustes(gemini_clave="AIzaOtraClave"))
    assert e.value.codigo == "clave_invalida" and "AIzaOtraClave" not in e.value.mensaje


def test_la_clave_nunca_sale_en_logs_ni_mensajes(gemini, caplog):
    caplog.set_level(logging.DEBUG)
    textos = []
    # Google (hipotéticamente) repite la clave en su mensaje: el programa no debe copiarla
    for estado, cuerpo in [(400, error_gemini(400, "INVALID_ARGUMENT", f"API key not valid: {CLAVE_DE_PRUEBA}")),
                           (403, error_gemini(403, "PERMISSION_DENIED", f"key {CLAVE_DE_PRUEBA} blocked")),
                           (429, error_gemini(429, "RESOURCE_EXHAUSTED", f"quota {CLAVE_DE_PRUEBA}")),
                           (500, error_gemini(500, "INTERNAL", f"boom {CLAVE_DE_PRUEBA}")), (418, f"tetera {CLAVE_DE_PRUEBA}")]:
        gemini.forzar(estado, cuerpo)
        if estado == 500:
            gemini.forzar(estado, cuerpo)
        with pytest.raises(remito.ErrorRemito) as e:
            remito.leer_remito(PNG, "image/png", CATALOGO, ajustes())
        textos.append(e.value.mensaje)
    leer(gemini, [item("GRIP", 1, "unidad", None, "grip", 0.9)])
    assert CLAVE_DE_PRUEBA not in caplog.text and CLAVE_DE_PRUEBA not in "\n".join(textos)
    assert remito.limpiar_clave(f"hola {CLAVE_DE_PRUEBA} chau", {"gemini_clave": CLAVE_DE_PRUEBA}) == "hola *** chau"


def test_proveedores_se_registran_con_un_decorador(gemini, monkeypatch):
    class Otro(remito.ProveedorRemito):
        def tiene_clave(self):
            return True

        def leer(self, imagen, mime, productos):
            return [{"texto": "GRIP", "cantidad": 2, "presentacion": "unidad", "unidades_por_presentacion": None, "producto_id": "grip", "confianza": 0.9, "dudoso": False}]
    monkeypatch.setitem(remito.PROVEEDORES, "otro", remito.proveedor("otro")(Otro))
    res = remito.leer_remito(PNG, "image/png", CATALOGO, dict(AJUSTES_POR_DEFECTO, remito_proveedor="otro"))
    assert res["proveedor"] == "otro" and res["lineas"][0]["sugerido_id"] == "grip" and not gemini.pedidos
    with pytest.raises(remito.ErrorRemito) as e:
        remito.leer_remito(PNG, "image/png", CATALOGO, dict(AJUSTES_POR_DEFECTO, remito_proveedor="../etc"))
    assert e.value.codigo == "proveedor" and "gemini" in e.value.mensaje


# ------------------------------------------------------------------------------------------------ configuración
def test_ajustes_nuevos_por_defecto():
    assert AJUSTES_POR_DEFECTO["gemini_clave"] == "" and AJUSTES_POR_DEFECTO["gemini_modelo"] == "gemini-flash-latest"
    assert AJUSTES_POR_DEFECTO["remito_proveedor"] == "gemini"
    ayuda = AJUSTES_POR_DEFECTO["_ayuda"]
    assert "gemini_clave" in ayuda and "gemini_modelo" in ayuda and "aistudio.google.com/apikey" in ayuda


def test_el_archivo_viejo_se_completa_sin_pisar_lo_que_habia(rutas):
    viejo = {"puerto": 9000, "copias_conservar_dias": 7, "nube_url": "https://x.example", "algo_mio": 1}
    rutas.ajustes.write_text(json.dumps(viejo), encoding="utf-8")
    a = leer_ajustes(rutas)
    assert a["puerto"] == 9000 and a["gemini_clave"] == ""
    en_disco = json.loads(rutas.ajustes.read_text(encoding="utf-8"))
    assert en_disco["puerto"] == 9000 and en_disco["nube_url"] == "https://x.example" and en_disco["algo_mio"] == 1
    assert "gemini_clave" in en_disco and "gemini_modelo" in en_disco and en_disco["_ayuda"] == AJUSTES_POR_DEFECTO["_ayuda"]
    mtime = rutas.ajustes.stat().st_mtime_ns
    leer_ajustes(rutas)
    assert rutas.ajustes.stat().st_mtime_ns == mtime, "si ya está completo no se vuelve a escribir"


def test_el_bloc_de_notas_con_marca_al_principio(rutas):
    rutas.ajustes.write_bytes(b"\xef\xbb\xbf" + json.dumps({"gemini_clave": CLAVE_DE_PRUEBA}).encode())
    assert remito.estado(rutas)["clave"] is True


def test_archivo_roto_se_avisa_distinto_a_falta_de_clave(rutas):
    rutas.ajustes.write_text('{"gemini_clave": "AIza... falta cerrar', encoding="utf-8")
    e = remito.estado(rutas)
    assert e["clave"] is False and e["config_rota"] is True and "error de escritura" in e["error"]
    with pytest.raises(remito.ErrorRemito) as err:
        remito.ajustes_de_remito(rutas)
    assert err.value.codigo == "config_rota"


# ------------------------------------------------------------------------------------------------ endpoint
@pytest.fixture()
def srv(rutas, gemini, monkeypatch):
    monkeypatch.setattr("predio.servidor.abrir_carpeta", lambda ruta: None)
    rutas.ajustes.write_text(json.dumps({"gemini_clave": CLAVE_DE_PRUEBA}), encoding="utf-8")
    app = app_mod.crear_aplicacion(rutas, dict(AJUSTES_POR_DEFECTO), "real")
    app.diario.ESPERA, app.diario.ESPERA_MAXIMA = 0.05, 0.2
    app.diario.iniciar()
    s = Servidor(app, 0)
    s.iniciar()
    base = base_inicial()
    for pid, nombre, paq, pn, tipo in [("p3", "Alfajor triple", 24, "Caja × 24", "directo"), ("p4", "Salchicha", 6, "Paquete × 6", "insumo"),
                                       ("p5", "Pan de pancho", 6, "Paquete × 6", "insumo"), ("p6", "Coca-Cola 1,5 L", 6, "Pack × 6", "directo"),
                                       ("p7", "Turno de cancha", 1, "", "servicio")]:
        base["ops"].append(op("productos", pid, {"id": pid, "nombre": nombre, "catId": "c1", "tipo": tipo, "precio": 1000, "costo": 500, "stock": 0, "minimo": 0,
                                                 "activo": True, "paqueteUnidades": paq, "paqueteNombre": pn, "receta": []}))
    pedir(s, "POST", "/api/guardar", {**base, "base_rev": 0, "usuario": "Sistema"})
    yield s
    s.parar()
    app_mod.cerrar_aplicacion(app)


def pedir(s, metodo, ruta, cuerpo=None, token=True, cabeceras=None, crudo=False):
    datos = None if cuerpo is None else (cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode())
    h = {"Host": f"127.0.0.1:{s.puerto}"}
    if token:
        h["X-Predio-Token"] = s.app.token
    if datos is not None and not isinstance(cuerpo, bytes):
        h["Content-Type"] = "application/json"
    h.update(cabeceras or {})
    req = urllib.request.Request(f"http://127.0.0.1:{s.puerto}{ruta}", data=datos, method=metodo, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            b = r.read()
            return r.status, (b if crudo else json.loads(b or b"null")), r.headers
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b), e.headers
        except ValueError:
            return e.code, b, e.headers


def leer_foto(s, foto=PNG):
    return pedir(s, "POST", "/api/remito/leer", foto, cabeceras={"Content-Type": "application/octet-stream"})


def test_endpoint_lee_y_empareja_con_los_productos_de_la_base(srv, gemini):
    gemini.items = [item("COCA COLA 500CC", 2, "unidad", None, "p1", 0.95), item("ALFAJOR TRIPLE CJ X24", 1, "caja", 24, "p3", 0.9),
                    item("SALCHICHA VIENA PAQ X6", 3, "paquete", 6, "p4", 0.9), item("SERVILLETAS PAPEL X500", 2, "paquete", 500),
                    item("PANCHO", 5, "unidad", None, "p2", 0.9), item("TURNO CANCHA", 1, "unidad", None, "p7", 0.9)]
    estado, r, _ = leer_foto(srv)
    assert estado == 200 and r["ok"] is True and len(r["lineas"]) == 6
    coca, alfa, salch, serv, pancho, turno = r["lineas"]
    assert coca["sugerido_id"] == "p1" and coca["estado"] == "seguro"
    assert alfa["sugerido_id"] == "p3" and alfa["unidades"] == 24
    assert salch["sugerido_id"] == "p4" and salch["unidades"] == 18
    assert serv["estado"] == "ninguno" and serv["sugerido_id"] is None
    assert pancho["sugerido_id"] != "p2" and turno["sugerido_id"] != "p7", "una receta y un servicio no reciben stock"
    assert r["huella"] == hashlib.sha256(PNG).hexdigest()
    # la lista que viajó a la IA no tiene recetas ni servicios
    prompt = gemini.prompt_recibido()
    assert "p1 | Coca-Cola 500 ml |" in prompt and "p3 | Alfajor triple | Caja × 24 (24 u.)" in prompt
    assert "p2 |" not in prompt and "p7 |" not in prompt
    assert gemini.imagen_recibida() == PNG


def test_endpoint_acepta_jpeg_png_webp_y_rechaza_otra_cosa(srv, gemini):
    jpeg = b"\xff\xd8\xff\xe0" + b"\0" * 50
    webp = b"RIFF\x24\0\0\0WEBPVP8 " + b"\0" * 30
    for foto, mime in [(PNG, "image/png"), (jpeg, "image/jpeg"), (webp, "image/webp")]:
        assert leer_foto(srv, foto)[0] == 200
        assert gemini.ultimo()["cuerpo"]["contents"][0]["parts"][1]["inline_data"]["mime_type"] == mime
    n = len(gemini.pedidos)
    for malo in (b"GIF89a....", b"%PDF-1.4 ...", b"<svg></svg>", b"hola"):
        estado, r, _ = leer_foto(srv, malo)
        assert estado == 415 and r["codigo"] == "formato" and "JPG, PNG o WEBP" in r["error"]
    estado, r, _ = pedir(srv, "POST", "/api/remito/leer", b"", cabeceras={"Content-Type": "application/octet-stream"})
    assert estado == 400 and r["codigo"] == "sin_foto"
    assert len(gemini.pedidos) == n, "lo que no es una foto no gasta el cupo de la IA"


def test_endpoint_foto_demasiado_grande(srv, gemini):
    grande = PNG + b"\0" * (remito.MAX_IMAGEN + 10)
    estado, r, _ = leer_foto(srv, grande)
    assert estado == 413 and r["codigo"] == "imagen_grande" and "demasiado grande" in r["error"]
    assert not gemini.pedidos
    assert pedir(srv, "GET", "/api/ping", token=False)[0] == 200, "el programa sigue andando"


def test_endpoint_pide_token_como_los_demas(srv, gemini):
    estado, r, _ = pedir(srv, "POST", "/api/remito/leer", PNG, token=False)
    assert estado == 403 and not gemini.pedidos
    assert pedir(srv, "GET", "/api/remito/estado", token=False)[0] == 403


def test_endpoint_sin_clave(srv, gemini, rutas):
    rutas.ajustes.write_text(json.dumps({"gemini_clave": ""}), encoding="utf-8")
    estado, r, _ = leer_foto(srv)
    assert estado == 400 and r["codigo"] == "sin_clave" and "clave" in r["error"] and not gemini.pedidos
    e = pedir(srv, "GET", "/api/remito/estado")[1]
    assert e["clave"] is False and e["archivo"].endswith("configuracion.json") and e["config_rota"] is False


def test_la_clave_se_toma_sin_reiniciar(srv, gemini, rutas):
    rutas.ajustes.write_text(json.dumps({"gemini_clave": ""}), encoding="utf-8")
    assert pedir(srv, "GET", "/api/remito/estado")[1]["clave"] is False
    rutas.ajustes.write_text(json.dumps({"gemini_clave": CLAVE_DE_PRUEBA}), encoding="utf-8")
    assert pedir(srv, "GET", "/api/remito/estado")[1]["clave"] is True
    gemini.items = []
    assert leer_foto(srv)[0] == 200


def test_endpoint_traduce_los_errores_de_google(srv, gemini):
    for forzado, estado_esperado, codigo in [
        ((400, error_gemini(400, "INVALID_ARGUMENT", "API key not valid.")), 502, "clave_invalida"),
        ((403, error_gemini(403, "PERMISSION_DENIED", "no")), 502, "clave_invalida"),
        ((429, error_gemini(429, "RESOURCE_EXHAUSTED", "per minute")), 429, "cupo_minuto"),
        ((429, error_gemini(429, "RESOURCE_EXHAUSTED", "Quota exceeded: GenerateRequestsPerDay")), 429, "cupo_dia"),
        ((200, "{ roto"), 502, "ilegible"),
    ]:
        gemini.forzar(*forzado)
        estado, r, _ = leer_foto(srv)
        assert (estado, r["codigo"], r["ok"]) == (estado_esperado, codigo, False), r
        assert CLAVE_DE_PRUEBA not in json.dumps(r)
    for _ in range(2):
        gemini.forzar(500, error_gemini(500, "INTERNAL", "x"))
    estado, r, _ = leer_foto(srv)
    assert estado == 502 and r["codigo"] == "servicio"


def test_endpoint_una_lectura_a_la_vez(srv, gemini):
    gemini.items = []
    gemini.retardo = 0.8
    resultados = []
    hilo = threading.Thread(target=lambda: resultados.append(leer_foto(srv)))
    hilo.start()
    time.sleep(0.3)
    estado, r, _ = leer_foto(srv)
    hilo.join()
    assert estado == 409 and r["codigo"] == "ocupado" and resultados[0][0] == 200
    assert leer_foto(srv)[0] == 200, "después de terminar se puede leer otra"
    assert len(gemini.pedidos) == 2


def test_la_clave_no_llega_a_la_pantalla_ni_a_la_base_ni_a_los_logs(srv, gemini, rutas, caplog):
    caplog.set_level(logging.DEBUG)
    gemini.items = [item("GRIP", 1, "unidad", None, "p1", 0.5)]
    leer_foto(srv)
    gemini.forzar(400, error_gemini(400, "INVALID_ARGUMENT", f"API key not valid {CLAVE_DE_PRUEBA}"))
    leer_foto(srv)
    vistos = [pedir(srv, "GET", "/", token=False, crudo=True)[1], pedir(srv, "GET", "/api/salud")[1], pedir(srv, "GET", "/api/remito/estado")[1],
              pedir(srv, "GET", "/api/estado")[1], pedir(srv, "GET", "/api/auditoria?limite=500")[1], pedir(srv, "GET", "/api/ping", token=False)[1]]
    for v in vistos:
        assert CLAVE_DE_PRUEBA.encode() not in (v if isinstance(v, bytes) else json.dumps(v).encode())
    assert CLAVE_DE_PRUEBA not in caplog.text
    for archivo in rutas.datos.glob("predio.db*"):
        assert CLAVE_DE_PRUEBA.encode() not in archivo.read_bytes()
    html = pedir(srv, "GET", "/", token=False, crudo=True)[1].decode()
    assert '"remito": {"clave": true}' in html
    assert pedir(srv, "GET", "/api/salud")[1]["remito_clave"] is True


def test_la_pantalla_se_entera_de_si_hay_clave(srv, rutas):
    rutas.ajustes.write_text(json.dumps({"gemini_clave": ""}), encoding="utf-8")
    html = pedir(srv, "GET", "/", token=False, crudo=True)[1].decode()
    assert '"remito": {"clave": false}' in html
    assert pedir(srv, "GET", "/api/salud")[1]["remito_clave"] is False


def test_endpoint_con_archivo_de_ajustes_roto(srv, gemini, rutas):
    rutas.ajustes.write_text('{"gemini_clave": "AIza', encoding="utf-8")
    estado, r, _ = leer_foto(srv)
    assert estado == 400 and r["codigo"] == "config_rota" and not gemini.pedidos
    assert pedir(srv, "GET", "/", token=False, crudo=True)[0] == 200, "la pantalla principal sigue abriendo"
