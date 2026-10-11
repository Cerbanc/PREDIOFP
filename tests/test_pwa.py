"""Web instalable en el celular (reportes y turnos): íconos, manifest, service worker, cartel de instalación.

Las pruebas que sólo leen archivos corren siempre. Las que necesitan la nube levantan su propia copia local (Worker + base D1 de
prueba, con wrangler en un puerto libre) y se saltean solas si falta wrangler o node/playwright.
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
NUBE = RAIZ / "nube"
PLAYWRIGHT = os.environ.get("PLAYWRIGHT_PATH") or "/opt/node22/lib/node_modules/playwright"
WRANGLER = next((p for p in (os.environ.get("WRANGLER_PATH"), "/tmp/w/node_modules/.bin/wrangler", shutil.which("wrangler")) if p and Path(p).exists()), None)
PAGINAS = {"reportes": NUBE / "src" / "pagina.html", "turnos": NUBE / "src" / "pagina_turnos.html"}
ICONOS = {"icono-192.png": (192, 6), "icono-512.png": (512, 6), "icono-maskable-512.png": (512, 2), "apple-touch-icon.png": (180, 2)}   # lado, tipo de color PNG (2 = sin transparencia, 6 = con)
CLAVE_PC, CLAVE_VER = "tokenpc", "clave123"

necesita_nube = pytest.mark.skipif(not WRANGLER, reason="falta wrangler (nube local)")
necesita_navegador = pytest.mark.skipif(not WRANGLER or not shutil.which("node") or not Path(PLAYWRIGHT).exists(), reason="falta wrangler, node o playwright")


def leer(ruta):
    return Path(ruta).read_text(encoding="utf-8")


def iconos_embebidos():
    return {n: base64.b64decode(b) for n, b in re.findall(r'"([\w.-]+\.png)":\s*"([A-Za-z0-9+/=]+)"', leer(NUBE / "src" / "iconos.js"))}


def datos_png(png):
    """(ancho, alto, tipo de color, ¿tiene transparencia por bloque tRNS?) de un PNG, leyendo el encabezado."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "no es PNG"
    ancho, alto, _prof, tipo = struct.unpack(">IIBB", png[16:26])
    return ancho, alto, tipo, b"tRNS" in png


# --------------------------------------------------------------------------------------------- sin nube: sólo archivos

def test_las_dos_paginas_tienen_la_cabeza_y_el_mismo_bloque_de_instalacion():
    bloques = {}
    for nombre, ruta in PAGINAS.items():
        html = leer(ruta)
        cabeza = html.split("</head>")[0]
        for pieza in ('<link rel="manifest" href="/manifest.webmanifest">', '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
                      '<meta name="apple-mobile-web-app-capable" content="yes">', '<meta name="apple-mobile-web-app-title" content="Predio">',
                      '<meta name="apple-mobile-web-app-status-bar-style"', '<meta name="mobile-web-app-capable" content="yes">'):
            assert pieza in cabeza, f"{nombre}: falta {pieza} en el <head>"
        assert 'name="theme-color" content="#f4f6f5" media="(prefers-color-scheme: light)"' in cabeza
        assert 'name="theme-color" content="#0f1714" media="(prefers-color-scheme: dark)"' in cabeza
        assert 'rel="icon" type="image/png" href="/icono-192.png"' in cabeza
        b = re.findall(r"<!-- PWA-INICIO.*?<!-- PWA-FIN -->", html, re.S)
        assert len(b) == 2, f"{nombre}: tiene que haber un bloque en la cabeza y otro al final"
        assert html.index(b[0]) < html.index("</head>") < html.index(b[1]) < html.index("</body>")
        assert '<script>' in b[1] and 'getElementById("app")' in b[1]
        bloques[nombre] = b
    assert bloques["reportes"] == bloques["turnos"], "el bloque de instalación tiene que ser idéntico en Reportes y en Turnos"


def test_el_cartel_respeta_lo_pedido_por_el_dueno():
    """Condiciones del cartel, leídas del propio script (el comportamiento real lo prueba el navegador, más abajo)."""
    codigo = re.findall(r"<!-- PWA-INICIO.*?<!-- PWA-FIN -->", leer(PAGINAS["reportes"]), re.S)[1]
    assert "(pointer: coarse)" in codigo and "Android|Mobile" in codigo                       # sólo celular o tablet táctil
    assert "display-mode: standalone" in codigo and "navigator.standalone" in codigo           # ya abierta como app: nada
    assert "localStorage" in codigo and "a.hidden" in codigo                                   # descarte recordado; no en la pantalla de la clave
    assert "beforeinstallprompt" in codigo and "Agregar a pantalla de inicio" in codigo and "Compartir" in codigo
    assert "http" not in codigo.replace("http://www.w3.org", ""), "nada de pedidos a otros sitios"


def test_los_iconos_embebidos_son_png_del_tamano_justo():
    iconos = iconos_embebidos()
    assert set(iconos) == set(ICONOS)
    for nombre, (lado, tipo) in ICONOS.items():
        ancho, alto, t, trns = datos_png(iconos[nombre])
        assert (ancho, alto) == (lado, lado), nombre
        if tipo == 2:      # el de iPhone y el «maskable» van sin transparencia (si no, iOS les pone fondo negro)
            assert t in (0, 2) and not trns, f"{nombre} no puede tener transparencia"
        else:
            assert t == 6, f"{nombre} conserva la transparencia del isotipo"


def test_el_generador_de_iconos_reproduce_los_iconos_embebidos(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    salida = tmp_path / "iconos.js"
    r = subprocess.run([sys.executable, str(NUBE / "tools" / "hacer_iconos.py"), "--salida", str(salida)], capture_output=True, text=True, cwd=RAIZ, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    nuevos = {n: base64.b64decode(b) for n, b in re.findall(r'"([\w.-]+\.png)":\s*"([A-Za-z0-9+/=]+)"', leer(salida))}
    viejos = iconos_embebidos()
    for nombre in ICONOS:      # se comparan los píxeles (los bytes del PNG dependen de la versión de zlib)
        a, b = pil.open(io.BytesIO(nuevos[nombre])).convert("RGBA"), pil.open(io.BytesIO(viejos[nombre])).convert("RGBA")
        assert a.size == b.size and a.tobytes() == b.tobytes(), f"{nombre}: iconos.js quedó viejo; corré nube/tools/hacer_iconos.py"


def test_el_isotipo_del_maskable_cabe_en_la_zona_segura():
    """Android recorta el ícono «maskable»: la P y el punto tienen que quedar dentro del círculo central (80 % del lado)."""
    pil = pytest.importorskip("PIL.Image")
    im = pil.open(io.BytesIO(iconos_embebidos()["icono-maskable-512.png"])).convert("RGB")
    fondo = im.getpixel((0, 0))
    claros = [(x, y) for y in range(0, 512, 2) for x in range(0, 512, 2) if sum(abs(a - b) for a, b in zip(im.getpixel((x, y)), fondo)) > 60]
    assert claros, "se ve algo más que el fondo"
    radio = max(((x - 256) ** 2 + (y - 256) ** 2) ** 0.5 for x, y in claros)
    assert radio <= 0.40 * 512, f"la P llega a {radio:.0f}px del centro; el máximo seguro es {0.40 * 512:.0f}"


# --------------------------------------------------------------------------------------------- con la nube local

class NubeLocal:
    def __init__(self, carpeta):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.puerto = s.getsockname()[1]
        s.close()
        self.base = f"http://127.0.0.1:{self.puerto}"
        self.carpeta, self.proc = Path(carpeta), None
        # se trabaja sobre una copia: wrangler deja carpetas temporales y no tienen que quedar en el repositorio
        shutil.copytree(NUBE / "src", self.carpeta / "nube" / "src")
        shutil.copy(RAIZ / "wrangler.toml", self.carpeta / "wrangler.toml")
        self.env = dict(os.environ, CI="1", WRANGLER_SEND_METRICS="false")
        persistir = str(self.carpeta / "datos")
        huella = lambda t: hashlib.sha256(t.encode()).hexdigest()
        # una sola pasada: tablas + las dos claves de prueba (cada llamada a wrangler tarda varios segundos)
        (self.carpeta / "inicio.sql").write_text(leer(NUBE / "schema.sql") + f"\nINSERT OR REPLACE INTO config VALUES ('hash_pc','{huella(CLAVE_PC)}'),('hash_ver','{huella(CLAVE_VER)}');\n", encoding="utf-8")
        r = subprocess.run([WRANGLER, "d1", "execute", "DB", "--local", "--persist-to", persistir, "--file", "inicio.sql"], cwd=self.carpeta, env=self.env, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
        self.log = open(self.carpeta / "wrangler.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen([WRANGLER, "dev", "--local", "--port", str(self.puerto), "--persist-to", persistir], cwd=self.carpeta, env=self.env,
                                     stdout=self.log, stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"))
        for _ in range(150):
            try:
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(self.base + "/manifest.webmanifest", timeout=2).read()
                return
            except Exception:
                if self.proc.poll() is not None:
                    break
                time.sleep(1)
        self.parar()
        pytest.fail("la nube local no arrancó:\n" + (self.carpeta / "wrangler.log").read_text(encoding="utf-8", errors="replace")[-2000:])

    def parar(self):
        if self.proc and self.proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
                else:
                    import signal
                    os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=20)
            except Exception:
                self.proc.kill()
        self.log.close()

    def pedir(self, ruta, metodo="GET", cuerpo=None, cookie=None, cabeceras=None):
        """Devuelve (estado, cabeceras, cuerpo en bytes); no sigue redirecciones ni usa el proxy del sistema."""
        class SinRedireccion(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        h = dict(cabeceras or {})
        if cookie:
            h["cookie"] = f"s={cookie}"
        req = urllib.request.Request(self.base + ruta, method=metodo, data=cuerpo, headers=h)
        try:
            r = urllib.request.build_opener(urllib.request.ProxyHandler({}), SinRedireccion).open(req, timeout=20)
        except urllib.error.HTTPError as e:
            r = e
        return r.status if hasattr(r, "status") else r.code, r.headers, r.read()

    def entrar(self, clave=CLAVE_VER):
        estado, cab, _ = self.pedir("/entrar", "POST", f"clave={clave}".encode(), cabeceras={"content-type": "application/x-www-form-urlencoded"})
        m = re.search(r"(?:^|, )s=([^;]+)", "; ".join(cab.get_all("set-cookie") or []))
        return estado, cab, (m.group(1) if m else None)


@pytest.fixture(scope="module")
def nube(tmp_path_factory):
    if not WRANGLER:
        pytest.skip("falta wrangler (nube local)")
    n = NubeLocal(tmp_path_factory.mktemp("nube"))
    yield n
    n.parar()


@necesita_nube
def test_manifest_publico_y_completo(nube):
    estado, cab, cuerpo = nube.pedir("/manifest.webmanifest")           # sin sesión
    assert estado == 200 and cab["content-type"].startswith("application/manifest+json") and cab["x-content-type-options"] == "nosniff"
    m = json.loads(cuerpo)
    assert (m["name"], m["short_name"], m["start_url"], m["scope"], m["display"], m["lang"]) == ("Predio", "Predio", "/", "/", "standalone", "es-AR")
    claro = re.search(r"--bg:(#[0-9a-f]{6})", leer(PAGINAS["reportes"])).group(1)
    assert m["background_color"] == claro and m["theme_color"] == claro, "los colores del manifest son los del diseño claro"
    assert {(i["sizes"], i["purpose"]) for i in m["icons"]} == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}
    for i in m["icons"]:        # cada ícono del manifest se baja sin sesión, es PNG y del tamaño que dice
        e, c, datos = nube.pedir(i["src"])
        assert e == 200 and c["content-type"] == "image/png" and "max-age" in c["cache-control"], i
        lado = int(i["sizes"].split("x")[0])
        assert datos_png(datos)[:2] == (lado, lado)
        assert datos == iconos_embebidos()[i["src"].lstrip("/")], "lo que sirve el Worker es lo embebido, sin alterar"


@necesita_nube
def test_icono_de_iphone_y_service_worker_son_publicos(nube):
    estado, cab, datos = nube.pedir("/apple-touch-icon.png")
    assert estado == 200 and cab["content-type"] == "image/png" and datos_png(datos) == (180, 180, 2, False)
    estado, cab, js = nube.pedir("/sw.js")
    assert estado == 200 and cab["content-type"].startswith("text/javascript") and "no-cache" in cab["cache-control"] and cab["x-content-type-options"] == "nosniff"
    texto = js.decode()
    assert 'addEventListener("fetch"' in texto and "Sin conexión" in texto
    assert not re.search(r"caches\.(open|put|add|match)|\.addAll|\.put\(", texto), "el service worker no guarda nada"
    estado, _, vacio = nube.pedir("/sw.js", "HEAD")
    assert estado == 200 and vacio == b""


@necesita_nube
def test_los_datos_y_la_clave_siguen_protegidos(nube):
    for ruta in ("/api/dias", "/api/dia/2026-01-01", "/excel/2026-01-01", "/api/turnos?fecha=2026-01-01", "/api/turnos/registro", "/otra-cosa"):
        estado, _, cuerpo = nube.pedir(ruta)
        assert estado == 401 and b"no autorizado" in cuerpo, ruta
    assert nube.pedir("/api/subir", "POST", b"{}")[0] == 401 and nube.pedir("/api/sync")[0] == 401
    # lo público es sólo lo del manifest: otros métodos o nombres parecidos no se abren
    for ruta, metodo in (("/manifest.webmanifest", "POST"), ("/sw.js", "POST"), ("/icono-192.png", "DELETE"), ("/icono-999.png", "GET"), ("/iconos.js", "GET"), ("/pwa.js", "GET"), ("/__proto__", "GET")):
        assert nube.pedir(ruta, metodo, b"x" if metodo != "GET" else None)[0] == 401, (ruta, metodo)
    # las pantallas sin sesión muestran sólo la clave (nada de datos adentro) y el CSP deja registrar el service worker del propio sitio
    for ruta in ("/", "/turnos"):
        estado, cab, cuerpo = nube.pedir(ruta)
        html = cuerpo.decode()
        assert estado == 200 and 'name="clave"' in html and "__DENTRO__" not in html and "__MAL__" not in html and "no-store" in cab["cache-control"]
        csp = cab["content-security-policy"]
        assert "worker-src 'self'" in csp and "default-src 'self'" in csp and "connect-src" not in csp
        assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    # clave mala: sin cookie; clave buena: cookie y datos; salir: borra la cookie (antes daba error del servidor)
    estado, cab, cookie = nube.entrar("mala")
    assert estado == 303 and cookie is None and cab["location"].endswith("?mal=1")
    estado, cab, cookie = nube.entrar()
    assert estado == 303 and cookie and "HttpOnly" in cab["set-cookie"]
    assert nube.pedir("/api/dias", cookie=cookie)[0] == 200
    estado, cab, _ = nube.pedir("/salir", cookie=cookie)
    assert estado == 303 and cab["location"] == "/" and "Max-Age=0" in cab["set-cookie"] and "HttpOnly" in cab["set-cookie"]


# --------------------------------------------------------------------------------------------- navegador real

@necesita_navegador
def test_cartel_de_instalacion_en_navegador_real(tmp_path):
    """Celular Android, iPhone y PC; modo app; descarte recordado; claro y oscuro; service worker; y sin internet. Va con su propia nube porque
    en la segunda fase se la apaga (para ver el aviso «Sin conexión» con el service worker ya instalado)."""
    n = NubeLocal(tmp_path / "nube")
    try:
        env = dict(os.environ, BASE=n.base, CLAVE=CLAVE_VER, PERFIL=str(tmp_path / "perfil"), FOTOS=os.environ.get("E2E_FOTOS", ""), PLAYWRIGHT_PATH=PLAYWRIGHT)
        r = subprocess.run(["node", str(RAIZ / "tests" / "e2e" / "pwa.js")], env=env, capture_output=True, text=True, timeout=400)
        assert r.returncode == 0, r.stdout + r.stderr
        n.parar()
        r = subprocess.run(["node", str(RAIZ / "tests" / "e2e" / "pwa.js")], env=dict(env, FASE="sin_internet"), capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        n.parar()
