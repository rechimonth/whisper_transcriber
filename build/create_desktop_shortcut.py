"""
build/create_desktop_shortcut.py
Genera el archivo ejecutar_app.bat en la raiz del proyecto
y crea un acceso directo (.lnk) en el Escritorio del usuario.
"""

import os
import sys
from pathlib import Path

# La raiz del proyecto está un nivel por encima de /build
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. Generar ejecutar_app.bat
# ---------------------------------------------------------------------------
def create_batch_file() -> Path:
    """
    Escribe ejecutar_app.bat en la raiz del proyecto.
    Usa 'start "" /B pythonw.exe main.py' para que la ventana CMD
    se cierre tan pronto como la GUI abre sin errores.
    """
    batch_path = PROJECT_ROOT / "ejecutar_app.bat"
    lines = [
        "@echo off",
        f'cd /d "{PROJECT_ROOT}"',
        "call venv\\Scripts\\activate.bat",
        'start "" /B venv\\Scripts\\pythonw.exe main.py',
    ]
    batch_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    print(f"[OK] Batch creado : {batch_path}")
    return batch_path


# ---------------------------------------------------------------------------
# 2. Crear acceso directo en el Escritorio
# ---------------------------------------------------------------------------
def create_desktop_shortcut(batch_path: Path) -> Path:
    """
    Crea Transcriptor de Video IA.lnk en el Escritorio usando win32com.
    Si pywin32 no está disponible, genera un PowerShell equivalente como fallback.
    """
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
        lnk.save()
        print(f"[OK] Acceso directo creado (win32com): {shortcut_path}")
        return shortcut_path
    except ImportError:
        print("[WARN] pywin32 no disponible, usando PowerShell como alternativa...")

    # --- Intento 2: PowerShell (siempre disponible en Windows 10) ---
    import subprocess

    ps_script = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{shortcut_path}'); "
        f"$s.TargetPath = '{batch_path}'; "
        f"$s.WorkingDirectory = '{PROJECT_ROOT}'; "
        f"$s.WindowStyle = 1; "
        f"$s.Description = 'Transcriptor de Video IA'; "
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    try:
        batch_path = create_batch_file()
        shortcut_path = create_desktop_shortcut(batch_path)
        print("\n¡Listo! Puedes abrir la aplicacion desde tu Escritorio.")
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
