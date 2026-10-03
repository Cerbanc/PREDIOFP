# -*- mode: python ; coding: utf-8 -*-
# Se arma una CARPETA (no un solo .exe): abre más rápido en una PC vieja y los antivirus se quejan menos.
import os
from PyInstaller.utils.hooks import collect_submodules

raiz = os.path.abspath(os.path.join(SPECPATH, '..'))
icono = os.path.join(SPECPATH, 'predio.ico')

a = Analysis(
    [os.path.join(raiz, 'predio_launcher.py')],
    pathex=[raiz],
    datas=[(os.path.join(raiz, 'predio', 'web'), os.path.join('predio', 'web'))],
    hiddenimports=collect_submodules('openpyxl'),
    excludes=['tkinter', 'unittest', 'pydoc', 'test', 'distutils', 'setuptools', 'pip', 'numpy', 'pandas', 'matplotlib', 'PIL'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Predio', console=False, icon=icono if os.path.exists(icono) else None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Predio')
