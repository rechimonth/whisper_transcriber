# -*- mode: python ; coding: utf-8 -*-
"""Spec de release del cliente desktop TranscriptorVideoIA.

Uso:  pyinstaller build_app.spec
Salida: dist/TranscriptorVideoIA/TranscriptorVideoIA.exe (onedir, windowed).

Incluye: assets/ (logos y fondos), paquete customtkinter completo (temas),
faster_whisper + ctranslate2 + tkinterdnd2. Icono .exe convertido al vuelo
desde assets/logo_main.png via Pillow.
"""
import os
from pathlib import Path

import customtkinter
from PyInstaller.utils.hooks import collect_all

# SPECPATH: global que PyInstaller 6 inyecta (dir del .spec = raiz del proyecto).
ROOT = Path(SPECPATH)  # noqa: F821 - global de PyInstaller
ASSETS = ROOT / "assets"
BUILD_DIR = ROOT / "build"

# --- Icono: PNG -> ICO al vuelo (PyInstaller exige .ico en Windows) ---
ICON_PNG = ASSETS / "logo_main.png"
ICON_ICO = BUILD_DIR / "app_icon_frozen.ico"
if ICON_PNG.exists():
    from PIL import Image

    with Image.open(ICON_PNG) as im:
        im.convert("RGB").save(
            ICON_ICO, format="ICO", sizes=[(256, 256), (128, 128),
                                           (64, 64), (48, 48),
                                           (32, 32), (16, 16)]
        )
else:
    ICON_ICO = ASSETS / "app_icon.ico"

# --- customtkinter: ruta dinamica del entorno actual ---
CTK_DIR = Path(customtkinter.__file__).resolve().parent

datas = [(str(ASSETS), "assets")]
binaries = []
hiddenimports = []
for package in ("customtkinter", "faster_whisper", "ctranslate2", "tkinterdnd2"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
# Carpeta de temas/assets de customtkinter (ruta del entorno activo).
ctk_assets = CTK_DIR / "assets"
if ctk_assets.is_dir():
    datas.append((str(ctk_assets), os.path.join("customtkinter", "assets")))

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["backend", "tests", "pytest", "uvicorn", "alembic"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TranscriptorVideoIA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed: sin terminal negra de fondo
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_ICO) if Path(ICON_ICO).exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TranscriptorVideoIA",
)
