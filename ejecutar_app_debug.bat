@echo off
REM Ejecuta la app en consola (python.exe) para ver errores por stdout/stderr.
setlocal
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] El entorno virtual "venv" no existe.
    echo         Ejecuta primero ejecutar_app.bat para crearlo.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
venv\Scripts\python.exe main.py
echo.
echo [INFO] La aplicacion se cerro. Revisa arriba si hubo errores.
pause
endlocal
