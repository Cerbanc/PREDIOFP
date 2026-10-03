"""Abre la pantalla del programa en una ventana propia (sin barra de direcciones ni pestañas).

Usa Microsoft Edge (viene con Windows 10) o Google Chrome en "modo aplicación", con un perfil aparte
para que sea una ventana independiente y se pueda saber cuándo el usuario la cerró.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

log = logging.getLogger("predio.ventana")


def buscar_navegador() -> str | None:
    candidatos: list[str] = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            candidatos += [
                os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
            ]
    else:
        for nombre in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            ruta = shutil.which(nombre)
            if ruta:
                candidatos.append(ruta)
    for c in candidatos:
        if os.path.isfile(c):
            return c
    return None


def abrir_ventana(url: str, perfil: Path, completa: bool = True) -> subprocess.Popen | None:
    """Devuelve el proceso de la ventana (para saber cuándo se cierra) o None si se abrió en el navegador común."""
    exe = buscar_navegador()
    if exe:
        perfil.mkdir(parents=True, exist_ok=True)
        args = [exe, f"--app={url}", f"--user-data-dir={perfil}", "--no-first-run", "--no-default-browser-check",
                "--disable-background-mode", "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
                "--disable-features=Translate,msEdgeShoppingUI", "--disable-sync", "--lang=es-AR"]
        if completa:
            args.append("--start-maximized")
        try:
            return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except OSError:
            log.exception("No se pudo abrir %s", exe)
    log.warning("No se encontró Edge ni Chrome: se abre en el navegador predeterminado")
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(      # type: ignore[attr-defined]
                0, "No encontré Microsoft Edge ni Google Chrome en esta PC, que son los que usa el programa para mostrar la pantalla.\n\n"
                   "Se va a intentar abrir con el navegador que tengas, pero si se ve mal o no funciona, instalá Google Chrome (es gratis) y volvé a abrir el programa.",
                "Caja del Predio", 0x30)
        except Exception:      # noqa: BLE001
            pass
    webbrowser.open(url)
    return None
