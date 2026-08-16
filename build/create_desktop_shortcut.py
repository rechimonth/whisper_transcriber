"""
build/create_desktop_shortcut.py
Genera el archivo ejecutar_app.bat en la raiz del proyecto,
crea el icono de la aplicacion (assets/app_icon.ico) y crea un acceso
directo en el Escritorio del usuario:
  - Windows: .lnk (via win32com o PowerShell) con icono personalizado.
  - Linux:   .desktop con icono personalizado.
"""

import os
import sys
import subprocess
from pathlib import Path

# La raiz del proyecto está un nivel por encima de /build
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_PATH = PROJECT_ROOT / "assets" / "app_icon.ico"
ICON_PATH_PNG = PROJECT_ROOT / "assets" / "app_icon.png"


# ---------------------------------------------------------------------------
# 1. Generar ejecutar_app.bat
# ---------------------------------------------------------------------------
def create_batch_file() -> Path:
    """
    Escribe ejecutar_app.bat en la raiz del proyecto.

    Usa %~dp0 (el directorio del propio .bat) en vez de una ruta absoluta
    para que el archivo sea portable y funcione en cualquier maquina.
    Crea el venv automaticamente si no existe y lanza la GUI con pythonw.exe.
    """
    batch_path = PROJECT_ROOT / "ejecutar_app.bat"
    lines = [
        "@echo off",
        "setlocal",
        'set "APP_DIR=%~dp0"',
        'cd /d "%APP_DIR%"',
        'if not exist "venv\\Scripts\\python.exe" (',
        '    echo [INFO] Creando entorno virtual "venv"...',
        "    where py >nul 2>nul",
        "    if %errorlevel%==0 (",
        "        py -m venv venv",
        "    ) else (",
        "        python -m venv venv",
        "    )",
        '    if not exist "venv\\Scripts\\python.exe" (',
        "        echo [ERROR] No se pudo crear el entorno virtual.",
        "        echo         Verifica que Python este instalado y en el PATH.",
        "        pause",
        "        exit /b 1",
        "    )",
        "    echo [INFO] Instalando dependencias desde requirements.txt...",
        '    "venv\\Scripts\\python.exe" -m pip install --upgrade pip',
        '    "venv\\Scripts\\python.exe" -m pip install -r requirements.txt',
        ")",
        "call venv\\Scripts\\activate.bat",
        'start "" /B venv\\Scripts\\pythonw.exe main.py',
        "endlocal",
    ]
    batch_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    print(f"[OK] Batch creado : {batch_path}")
    return batch_path


# ---------------------------------------------------------------------------
# 2. Generar el icono de la aplicacion
# ---------------------------------------------------------------------------
def create_icon() -> Path:
    """Genera assets/app_icon.ico (y .png) si aun no existen."""
    if ICON_PATH.exists() and ICON_PATH_PNG.exists():
        print(f"[OK] Icono ya existe: {ICON_PATH}")
        return ICON_PATH
    script = PROJECT_ROOT / "build" / "create_icon.py"
    if not script.is_file():
        raise FileNotFoundError(f"No se encontro {script}")
    subprocess.run([sys.executable, str(script)], check=True)
    return ICON_PATH


# ---------------------------------------------------------------------------
# 3. Crear acceso directo en el Escritorio
# ---------------------------------------------------------------------------
def create_desktop_shortcut(batch_path: Path, icon_path: Path) -> Path:
    """
    Crea un acceso directo en el Escritorio con icono personalizado.

    - Windows: 'Transcriptor de Video IA.lnk' via win32com (o PowerShell).
    - Linux:   'transcriptor-video-ia.desktop' en ~/.local/share/applications
               y se copia al Escritorio si existe.
    """
    if os.name == "nt":
        return _create_windows_shortcut(batch_path, icon_path)
    return _create_linux_shortcut(batch_path, icon_path)


def _create_windows_shortcut(batch_path: Path, icon_path: Path) -> Path:
    desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
    if not desktop.is_dir():
        raise FileNotFoundError(f"No se encontro el escritorio: {desktop}")

    shortcut_path = desktop / "Transcriptor de Video IA.lnk"

    # --- Intento 1: win32com (pywin32) ---
    try:
        from win32com.client import Dispatch

        ws = Dispatch("WScript.Shell")
        lnk = ws.CreateShortcut(str(shortcut_path))
        lnk.TargetPath = str(batch_path)
        lnk.WorkingDirectory = str(PROJECT_ROOT)
        lnk.WindowStyle = 1          # ventana normal
        lnk.Description = "Transcriptor de Video IA"
        if icon_path.exists():
            lnk.IconLocation = str(icon_path)
        lnk.save()
        print(f"[OK] Acceso directo creado (win32com): {shortcut_path}")
        return shortcut_path
    except ImportError:
        print("[WARN] pywin32 no disponible, usando PowerShell como alternativa...")

    # --- Intento 2: PowerShell (siempre disponible en Windows 10) ---
    icon_clause = f"$s.IconLocation = '{icon_path}'; " if icon_path.exists() else ""
    ps_script = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{shortcut_path}'); "
        f"$s.TargetPath = '{batch_path}'; "
        f"$s.WorkingDirectory = '{PROJECT_ROOT}'; "
        f"$s.WindowStyle = 1; "
        f"$s.Description = 'Transcriptor de Video IA'; "
        f"{icon_clause}"
        f"$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PowerShell fallo al crear el acceso directo:\n{result.stderr}"
        )
    print(f"[OK] Acceso directo creado (PowerShell): {shortcut_path}")
    return shortcut_path


def _create_linux_shortcut(batch_path: Path, icon_path: Path) -> Path:
    """Crea un .desktop para Linux. Como el .bat es de Windows, el lanzador
    ejecuta 'python main.py' del venv si existe, o 'python3 main.py'."""
    desktop_dir = Path.home() / "Desktop"
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)

    # El .bat no sirve en Linux: usar python directo.
    venv_py = PROJECT_ROOT / "venv" / "bin" / "python"
    python_exe = str(venv_py) if venv_py.exists() else "python3"
    # Linux prefiere PNG sobre ICO para los iconos .desktop.
    icon_str = str(ICON_PATH_PNG) if ICON_PATH_PNG.exists() else str(icon_path)

    name = "Transcriptor de Video IA"
    desktop_file = apps_dir / "transcriptor-video-ia.desktop"
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        "Comment=Transcripcion de audio y video con Whisper\n"
        f"Exec={python_exe} {PROJECT_ROOT / 'main.py'}\n"
        f"Path={PROJECT_ROOT}\n"
        f"Icon={icon_str}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;\n"
    )
    desktop_file.write_text(content, encoding="utf-8")
    desktop_file.chmod(0o755)

    # Copiar al Escritorio si existe (HOME/Desktop o escritorio localizado)
    placed = desktop_file
    for candidate in (desktop_dir, Path.home() / "Escritorio"):
        if candidate.is_dir():
            dst = candidate / "transcriptor-video-ia.desktop"
            dst.write_text(content, encoding="utf-8")
            dst.chmod(0o755)
            placed = dst
            break

    # Actualizar la base de datos de aplicaciones (no es fatal si falla)
    try:
        subprocess.run(["update-desktop-database", str(apps_dir)],
                       capture_output=True, check=False)
    except FileNotFoundError:
        pass

    print(f"[OK] Acceso directo creado (Linux .desktop): {placed}")
    return placed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    try:
        batch_path = create_batch_file()
        icon_path = create_icon()
        shortcut_path = create_desktop_shortcut(batch_path, icon_path)
        print("\n¡Listo! Puedes abrir la aplicacion desde tu Escritorio.")
        print(f"       Icono: {icon_path}")
        print(f"       Acceso directo: {shortcut_path}")
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
