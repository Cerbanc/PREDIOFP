@echo off
rem Arma el programa para Windows. Hace falta Python 3.11 instalado (sólo en la PC donde se arma, no en la del predio).
setlocal
cd /d "%~dp0\.."
if not exist .venv ( py -3.11 -m venv .venv || goto :error )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip || goto :error
pip install -r requirements-dev.txt || goto :error
python packaging\hacer_icono.py || goto :error
python -m pytest tests -q --ignore=tests\test_e2e.py || goto :error
pyinstaller packaging\predio.spec --noconfirm || goto :error
python packaging\probar_exe.py dist\Predio\Predio.exe || goto :error
echo.
echo Listo: dist\Predio\Predio.exe
echo Para generar el instalador, abrir packaging\instalador.iss con Inno Setup y compilar.
exit /b 0
:error
echo.
echo Algo fallo. Revisa los mensajes de arriba.
exit /b 1
