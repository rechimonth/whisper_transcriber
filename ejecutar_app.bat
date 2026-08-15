@echo off
REM Inicia el Transcriptor de Video IA de forma portable.
REM %~dp0 es el directorio donde reside este .bat (con la barra final).
setlocal
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

REM --- 1. Crear el entorno virtual si no existe -------------------------------
if not exist "venv\Scripts\python.exe" (
    echo [INFO] Creando entorno virtual "venv"...
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -m venv venv
    ) else (
        python -m venv venv
    )
    if not exist "venv\Scripts\python.exe" (
        echo [ERROR] No se pudo crear el entorno virtual.
        echo         Verifica que Python este instalado y en el PATH.
        pause
        exit /b 1
    )
    echo [INFO] Instalando dependencias desde requirements.txt...
    "venv\Scripts\python.exe" -m pip install --upgrade pip
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM --- 2. Activar el entorno virtual ------------------------------------------
call venv\Scripts\activate.bat

REM --- 3. Lanzar la aplicacion ------------------------------------------------
REM     Se usa pythonw.exe (sin consola) para que la GUI se vea limpia.
REM     Si la app no abre, ejecuta ejecutar_app_debug.bat para ver el error.
start "" /B venv\Scripts\pythonw.exe main.py
endlocal
