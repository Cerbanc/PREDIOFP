"""Punto de entrada para el instalador (PyInstaller) y para `python predio_launcher.py`."""
import multiprocessing
import sys

from predio.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
