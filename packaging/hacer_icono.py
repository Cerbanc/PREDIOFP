"""Genera predio.ico (círculo verde esmeralda sobre fondo oscuro) sin librerías externas."""
import struct
import sys
import zlib
from pathlib import Path


def png(tam: int) -> bytes:
    fondo, verde, claro = (18, 38, 31, 255), (10, 122, 90, 255), (211, 238, 227, 255)
    c, r_ext, r_int = (tam - 1) / 2, tam * 0.40, tam * 0.15
    filas = []
    for y in range(tam):
        fila = bytearray([0])
        for x in range(tam):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            esq = min(x, y, tam - 1 - x, tam - 1 - y)
            if esq < tam * 0.06 and False:
                px = (0, 0, 0, 0)
            elif d <= r_int:
                px = claro
            elif d <= r_ext:
                px = verde
            else:
                px = fondo
            fila += bytes(px)
        filas.append(bytes(fila))
    crudo = b"".join(filas)

    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", tam, tam, 8, 6, 0, 0, 0)) + trozo(b"IDAT", zlib.compress(crudo, 9)) + trozo(b"IEND", b"")


def main(destino: Path) -> None:
    tamanos = [16, 32, 48, 64, 128, 256]
    imagenes = [png(t) for t in tamanos]
    cab = struct.pack("<HHH", 0, 1, len(tamanos))
    offset = 6 + 16 * len(tamanos)
    entradas, cuerpo = b"", b""
    for t, img in zip(tamanos, imagenes):
        entradas += struct.pack("<BBBBHHII", t % 256, t % 256, 0, 0, 1, 32, len(img), offset + len(cuerpo))
        cuerpo += img
    destino.write_bytes(cab + entradas + cuerpo)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("predio.ico"))
    print("icono listo")
