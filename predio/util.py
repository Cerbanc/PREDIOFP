"""Funciones chicas compartidas: fechas, dinero, nombres de archivo."""
from __future__ import annotations

import datetime as dt
import re
import time
import unicodedata


def ahora_ms() -> int:
    return int(time.time() * 1000)


def dia_de(ts_ms: float) -> str:
    """AAAA-MM-DD en hora local (igual que hace la pantalla)."""
    return dt.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def hora_de(ts_ms: float, segundos: bool = False) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M:%S" if segundos else "%H:%M")


def fecha_hora(ts_ms: float) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000).strftime("%d/%m/%Y %H:%M")


def hm(minutos: int | float | None) -> str:
    """Minutos desde medianoche -> HH:MM (como en el calendario de turnos)."""
    if minutos is None:
        return ""
    m = int(minutos)
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def plata(n) -> str:
    """$ 12.345 (miles con punto, como se escribe en Argentina)."""
    try:
        v = round(float(n))
    except (TypeError, ValueError):
        return "$ 0"
    s = f"{abs(v):,}".replace(",", ".")
    return f"-$ {s}" if v < 0 else f"$ {s}"


def es_dia(texto) -> bool:
    return isinstance(texto, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto) is not None


def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(texto or "")) if unicodedata.category(c) != "Mn")


def normalizar(texto) -> str:
    """Minúsculas, sin tildes y con espacios simples: para comparar nombres."""
    return re.sub(r"\s+", " ", sin_acentos(texto).lower()).strip()


def nombre_seguro(texto: str) -> str:
    """Para armar nombres de archivo sin caracteres raros."""
    limpio = re.sub(r"[^A-Za-z0-9_.-]+", "_", sin_acentos(texto)).strip("_")
    return limpio or "archivo"
