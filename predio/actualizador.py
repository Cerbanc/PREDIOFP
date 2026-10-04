"""Actualizador: consulta si hay una versión nueva, la baja (una sola vez, el instalador completo) y la instala.

Sólo se conecta a internet cuando se lo piden (o una vez por día en segundo plano para avisar). Nunca descarga solo.
El instalador se verifica (tamaño y huella SHA-256 si GitHub la informa) y se ejecuta recién cuando el programa ya se cerró.
Los datos (C:\\Predio) no se tocan: la actualización sólo reemplaza el programa.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import VERSION

log = logging.getLogger("predio.actualizador")
API_POR_DEFECTO = "https://api.github.com"
PATRON_INSTALADOR = re.compile(r"^Instalar-Caja-del-Predio-.*\.exe$", re.I)
MAX_INSTALADOR = 300_000_000


class ErrorActualizacion(Exception):
    pass


def _api() -> str:
    return os.environ.get("PREDIO_API_GITHUB", API_POR_DEFECTO).rstrip("/")


def version_tupla(texto: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(texto).split("-")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def _pedir(url: str, timeout: float = 8.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "CajaPredio-actualizador", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - hosts verificados más abajo
        return r.read()


def buscar(repo: str) -> dict:
    """Devuelve {'hay': bool, 'actual': ..., 'version': ..., 'notas': ..., 'url': ..., 'bytes': ..., 'sha256': ...} o {'hay': False, 'error': ...}."""
    base = {"hay": False, "actual": VERSION}
    if not re.fullmatch(r"[A-Za-z0-9_][\w.-]*/[A-Za-z0-9_][\w.-]*", repo or ""):
        return {**base, "error": "El repositorio de actualizaciones está mal escrito en configuracion.json."}
    try:
        datos = json.loads(_pedir(f"{_api()}/repos/{repo}/releases/latest"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {**base, "error": "No hay versiones publicadas para descargar (o el repositorio es privado)."}
        return {**base, "error": f"GitHub respondió con un error ({e.code})."}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {**base, "error": "No se pudo consultar. ¿Hay internet?"}
    tag = str(datos.get("tag_name") or "")
    asset = next((a for a in datos.get("assets") or [] if PATRON_INSTALADOR.match(str(a.get("name") or ""))), None)
    res = {**base, "version": tag.lstrip("vV"), "notas": str(datos.get("body") or "")[:4000], "publicada": datos.get("published_at")}
    if asset is None:
        return {**res, "error": "La versión publicada no trae el instalador."}
    res.update({"url": asset.get("browser_download_url"), "bytes": asset.get("size"), "sha256": str(asset.get("digest") or "").removeprefix("sha256:") or None})
    res["hay"] = version_tupla(tag) > version_tupla(VERSION)
    return res


def _host_permitido(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    propio = (urllib.parse.urlsplit(_api()).hostname or "").lower()
    return urllib.parse.urlsplit(url).scheme in ("https", "http") and (host == propio or host.endswith(("github.com", "githubusercontent.com")))


def descargar(info: dict, carpeta: Path) -> Path:
    """Baja el instalador a `carpeta`, lo verifica y devuelve su ruta."""
    url = info.get("url") or ""
    if not _host_permitido(url):
        raise ErrorActualizacion("La dirección de descarga no es de GitHub: no se descarga.")
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = Path(urllib.parse.urlsplit(url).path).name
    if not PATRON_INSTALADOR.match(nombre):
        raise ErrorActualizacion("El archivo a descargar no parece el instalador.")
    destino = carpeta / nombre
    tmp = destino.with_name(destino.name + ".parte")
    h = hashlib.sha256()
    total = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CajaPredio-actualizador"})
        with urllib.request.urlopen(req, timeout=20) as r, open(tmp, "wb") as f:      # noqa: S310
            while True:
                trozo = r.read(262_144)
                if not trozo:
                    break
                total += len(trozo)
                if total > MAX_INSTALADOR:
                    raise ErrorActualizacion("El archivo es demasiado grande.")
                h.update(trozo)
                f.write(trozo)
    except ErrorActualizacion:
        tmp.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise ErrorActualizacion("Se cortó la descarga. Probá de nuevo cuando haya internet.") from e
    if info.get("bytes") and total != int(info["bytes"]):
        tmp.unlink(missing_ok=True)
        raise ErrorActualizacion("La descarga quedó incompleta. Probá de nuevo.")
    if info.get("sha256") and h.hexdigest().lower() != str(info["sha256"]).lower():
        tmp.unlink(missing_ok=True)
        raise ErrorActualizacion("La descarga no coincide con la huella de GitHub (puede estar dañada). No se instala.")
    os.replace(tmp, destino)
    return destino


def limpiar_viejos(carpeta: Path, conservar: Path | None = None) -> None:
    for p in carpeta.glob("Instalar-Caja-del-Predio-*"):
        if conservar is None or p != conservar:
            try:
                if time.time() - p.stat().st_mtime > 3 * 86400:
                    p.unlink()
            except OSError:
                pass


def lanzar_instalador(ruta: Path) -> None:
    """Ejecuta el instalador sin pantallas (se instala encima y vuelve a abrir el programa). Sólo se llama cuando el programa ya cerró."""
    import subprocess

    args = [str(ruta), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
