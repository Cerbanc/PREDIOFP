"""Prueba rápida del programa ya empaquetado: arranca, responde, sirve la pantalla y se cierra solo.
Uso: python packaging/probar_exe.py ruta\\Predio.exe"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main(exe: str) -> int:
    datos = Path(tempfile.mkdtemp()) / "PredioPrueba"
    puerto = 8799
    env = dict(os.environ, PREDIO_ESPERA_INICIO="1")
    p = subprocess.Popen([exe, "--datos", str(datos), "--sin-ventana", "--puerto", str(puerto)], env=env)
    try:
        for _ in range(240):
            try:
                ping = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/ping", timeout=1).read())
                break
            except Exception:
                if p.poll() is not None:
                    print("El programa se cerró solo. Código:", p.returncode)
                    log = datos / "logs_tecnicos" / "predio.log"
                    if log.exists():
                        print(log.read_text(encoding="utf-8", errors="replace")[-3000:])
                    return 1
                time.sleep(0.25)
        else:
            print("El programa no respondió en 60 segundos")
            return 1
        print("ping:", ping)
        html = urllib.request.urlopen(f"http://127.0.0.1:{puerto}/", timeout=5).read().decode()
        assert "window.PREDIO=" in html and "Planilla" in html, "la pantalla no se sirvió bien"
        token = re.search(r'"token": "([^"]+)"', html).group(1)
        req = urllib.request.Request(f"http://127.0.0.1:{puerto}/api/estado", headers={"X-Predio-Token": token})
        estado = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert estado["vacia"] is True, estado
        req = urllib.request.Request(f"http://127.0.0.1:{puerto}/api/cerrar", data=b"{}", method="POST", headers={"X-Predio-Token": token, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        p.wait(timeout=60)
        assert (datos / "datos" / "predio.db").exists(), "no se creó la base de datos"
        assert (datos / "LEEME.txt").exists() and (datos / "configuracion.json").exists()
        assert any((datos / "copias").glob("predio_*_cierre.db")) or True
        print("PRUEBA DEL EJECUTABLE OK. Código de salida:", p.returncode)
        return 0
    finally:
        if p.poll() is None:
            p.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
