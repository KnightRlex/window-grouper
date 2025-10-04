@echo off
TITLE Lanzador de Agrupador de Ventanas

REM --- Cambia al directorio donde se encuentra el archivo .bat ---
cd /d %~dp0

echo.
echo ==========================================
echo  Verificando e instalando dependencias...
echo ==========================================
echo.
REM --- Usamos 'python -m pip' para asegurar que instalemos la libreria
REM --- para la version correcta de Python que estamos usando.
python -m pip install psutil

echo.
echo ==========================================
echo  Iniciando el Agrupador de Ventanas...
echo ==========================================
echo.

python codigo.py

echo.
echo La aplicacion se ha cerrado.
pause