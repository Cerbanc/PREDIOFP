"""Genera los íconos de la web instalable (nube/src/iconos.js) a partir del isotipo del Predio.

Uso (desde la raíz del repositorio; hace falta Pillow, que es sólo para este script):
    python nube/tools/hacer_iconos.py [--origen nube/tools/isotipo.png] [--salida nube/src/iconos.js] [--png carpeta]

El Worker no tiene carpeta de archivos: los PNG van embebidos en base64 dentro de iconos.js.
Con el mismo isotipo y la misma versión de Pillow siempre sale el mismo archivo. Con --png además se guardan los PNG sueltos (para mirarlos).
"""
import argparse
import base64
import io
from pathlib import Path

from PIL import Image

AQUI = Path(__file__).resolve().parent
AZUL = (39, 56, 109)        # el azul del isotipo (el mismo del círculo): lo usan los fondos sólidos

# nombre público -> (lado en píxeles, qué tanto del lado ocupa el isotipo, fondo sólido o None = transparente)
ICONOS = {
    "icono-192.png": (192, 1.0, None),
    "icono-512.png": (512, 1.0, None),
    # «maskable»: Android lo recorta con la forma que quiera (círculo, gota, cuadrado). Lo importante tiene que caber en el
    # círculo central (80 % del lado): con el isotipo al 80 % la P ocupa menos del 37 % del lado desde el centro, o sea adentro
    # con margen, y el resto es el mismo azul.
    "icono-maskable-512.png": (512, 0.80, AZUL),
    # iPhone: sin transparencia (si no, iOS pone fondo negro) y sin redondear (lo redondea iOS)
    "apple-touch-icon.png": (180, 1.0, AZUL),
}


def hacer(isotipo, lado, escala, fondo):
    n = round(lado * escala)
    # se achica con la transparencia premultiplicada, si no el borde del círculo queda con un halo oscuro
    chico = isotipo.convert("RGBa").resize((n, n), Image.LANCZOS).convert("RGBA")
    if fondo is None:
        return chico
    lienzo = Image.new("RGBA", (lado, lado), fondo + (255,))
    lienzo.alpha_composite(chico, ((lado - n) // 2, (lado - n) // 2))
    return lienzo.convert("RGB")


def png(imagen):
    b = io.BytesIO()
    imagen.save(b, "PNG", optimize=True)
    return b.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--origen", default=str(AQUI / "isotipo.png"))
    ap.add_argument("--salida", default=str(AQUI.parent / "src" / "iconos.js"))
    ap.add_argument("--png", help="carpeta donde guardar también los PNG sueltos")
    a = ap.parse_args()
    isotipo = Image.open(a.origen).convert("RGBA")
    lineas = ["// Íconos de la web instalable (PNG en base64). Lo genera nube/tools/hacer_iconos.py: no lo edites a mano.", "export const ICONOS = {"]
    for nombre, (lado, escala, fondo) in ICONOS.items():
        datos = png(hacer(isotipo, lado, escala, fondo))
        if a.png:
            Path(a.png).mkdir(parents=True, exist_ok=True)
            (Path(a.png) / nombre).write_bytes(datos)
        lineas.append(f'  "{nombre}": "{base64.b64encode(datos).decode()}",')
        print(f"{nombre}: {lado}x{lado}, {len(datos)} bytes")
    lineas.append("};")
    Path(a.salida).write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("listo:", a.salida)


if __name__ == "__main__":
    main()
