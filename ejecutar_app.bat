@echo off
setlocal
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
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
call venv\Scripts\activate.bat
start "" /B venv\Scripts\pythonw.exe main.py
endlocal
