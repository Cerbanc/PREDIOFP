"""Copia LOCAL de la nube de turnos para las pruebas: el Worker real (nube/src/index.js) corriendo con wrangler y una base D1 simulada.

Se usa en test_nube_web.py. Si falta wrangler o node, esas pruebas se saltean solas.
Todo vive en una carpeta temporal (la base, el wrangler.toml y los registros): no se toca el repositorio.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
WRANGLER = os.environ.get("WRANGLER_PATH") or "/tmp/w/node_modules/.bin/wrangler"
CLAVE_PC = "tokenpc"          # clave de la PC (para /api/sync)
CLAVE_VER = "clave123"        # clave para mirar la web


def disponible() -> bool:
    return bool(shutil.which("node")) and Path(WRANGLER).exists()


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _entorno() -> dict:
    """Entorno de wrangler: sin avisos ni métricas, y sin proxy (todo es local; con proxy cada comando tarda ~10 s de más)."""
    e = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    return dict(e, CI="1", WRANGLER_SEND_METRICS="false", NO_PROXY="127.0.0.1,localhost")


class NubeLocal:
    def __init__(self, carpeta: Path, schema: str | None = None) -> None:
        self.carpeta = Path(carpeta)
        self.schema = schema if schema is not None else (RAIZ / "nube" / "schema.sql").read_text(encoding="utf-8")
        self.puerto = puerto_libre()
        self.url = f"http://127.0.0.1:{self.puerto}"
        self.proc: subprocess.Popen | None = None
        self.cookie = ""

    # ---- arranque y apagado ----
    def preparar(self) -> "NubeLocal":
        """Crea la configuración y la base (esquema + claves) sin prender nada."""
        self.carpeta.mkdir(parents=True, exist_ok=True)
        (self.carpeta / "wrangler.toml").write_text(
            f'name = "prediofp-prueba"\nmain = "{(RAIZ / "nube" / "src" / "index.js").as_posix()}"\ncompatibility_date = "2025-01-01"\n\n'
            '[[d1_databases]]\nbinding = "DB"\ndatabase_name = "predio-reportes"\ndatabase_id = "ad044e7e-aadd-41ea-b63e-0343ce1c8c5b"\n\n'
            '[[rules]]\ntype = "Text"\nglobs = ["**/*.html"]\nfallthrough = true\n', encoding="utf-8")
        self.sql(self.schema + f"\nINSERT OR REPLACE INTO config VALUES ('hash_pc','{sha(CLAVE_PC)}'),('hash_ver','{sha(CLAVE_VER)}');\n")
        return self

    def iniciar(self) -> "NubeLocal":
        self.preparar()
        log = open(self.carpeta / "dev.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [WRANGLER, "dev", "--local", "--port", str(self.puerto), "--inspector-port", str(puerto_libre()), "--persist-to", str(self.base)],
            cwd=self.carpeta, stdout=log, stderr=subprocess.STDOUT, env=_entorno(), start_new_session=True)
        for _ in range(120):
            try:
                urllib.request.urlopen(self.url + "/", timeout=2).read()
                break
            except urllib.error.HTTPError:
                break                                      # cualquier respuesta HTTP: ya está andando
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError("wrangler se cerró: " + (self.carpeta / "dev.log").read_text(encoding="utf-8"))
                time.sleep(0.5)
        else:
            self.parar()
            raise RuntimeError("la nube local no arrancó")
        self.cookie = self._entrar()
        return self

    @property
    def base(self) -> Path:
        return self.carpeta / "base"

    def sql(self, texto: str) -> None:
        """Corre SQL sobre la base local (con la nube apagada o prendida)."""
        archivo = self.carpeta / "tmp.sql"
        archivo.write_text(texto, encoding="utf-8")
        r = subprocess.run([WRANGLER, "d1", "execute", "DB", "--local", "--persist-to", str(self.base), "--file", str(archivo)],
                           cwd=self.carpeta, env=_entorno(), capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError("SQL falló: " + r.stdout + r.stderr)

    def consulta(self, texto: str) -> list[dict]:
        r = subprocess.run([WRANGLER, "d1", "execute", "DB", "--local", "--persist-to", str(self.base), "--json", "--command", texto],
                           cwd=self.carpeta, env=_entorno(), capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError("consulta falló: " + r.stdout + r.stderr)
        salida = r.stdout[r.stdout.index("\n[") + 1:] if "\n[" in r.stdout else r.stdout      # wrangler puede avisar algo antes del JSON
        return json.loads(salida)[0]["results"]

    def parar(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=15)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    # ---- pedidos ----
    def _entrar(self) -> str:
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=15)
        c.request("POST", "/entrar", body="clave=" + CLAVE_VER, headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        r.read()
        c.close()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and v.startswith("s="):
                return v.split(";")[0][2:]
        raise RuntimeError("no se pudo entrar a la nube local")

    def api(self, metodo: str, ruta: str, cuerpo: dict | None = None, pc: bool = False, cookie: str | None = None) -> tuple[int, dict]:
        """Pedido como lo haría el celular (cookie de sesión) o, con pc=True, como la PC del local (clave en la cabecera)."""
        h = {"Content-Type": "application/json"}
        if pc:
            h["Authorization"] = "Bearer " + CLAVE_PC
        else:
            h["Cookie"] = "s=" + (self.cookie if cookie is None else cookie)
        req = urllib.request.Request(self.url + ruta, method=metodo, data=json.dumps(cuerpo).encode() if cuerpo is not None else None, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def turnos(self, fecha: str) -> list[dict]:
        return self.api("GET", f"/api/turnos?fecha={fecha}")[1]["turnos"]

    def reservar(self, cancha: str, fecha: str, ini: int, cliente: str = "", tel: str = "", quien: str = "Ana", **extra) -> tuple[int, dict]:
        return self.api("POST", "/api/turnos", {"accion": "crear", "cancha": cancha, "fecha": fecha, "ini": ini, "cliente": cliente, "tel": tel, "quien": quien, **extra})
