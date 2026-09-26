"""
build/create_icon.py
Genera el icono de la aplicacion (assets/app_icon.ico y assets/app_icon.png).

El disenio representa un microfono estilizado sobre un fondo redondeado azul
oscuro (#1a2744), evocando transcripcion de audio. El .ico incluye varios
tamanoes (256, 128, 64, 48, 32, 16) para verse nítido en cualquier contexto
(Escritorio, barra de tareas, explorador).

Uso:
    python build/create_icon.py
"""
import os
import math
from PIL import Image, ImageDraw

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")
ICO_PATH = os.path.join(ASSETS_DIR, "app_icon.ico")
PNG_PATH = os.path.join(ASSETS_DIR, "app_icon.png")

BG_TOP = (31, 58, 95)        # #1f3a5f
BG_BOTTOM = (26, 39, 68)     # #1a2744
MIC_BODY = (236, 240, 241)   # blanco grisaceo
MIC_HIGHLIGHT = (255, 255, 255)
ACCENT = (46, 204, 113)      # #2ecc71 verde
WAVE = (208, 232, 255)       # azul claro

ICON_SIZES = [256, 128, 64, 48, 32, 16]


def _rounded_gradient_bg(size: int) -> Image.Image:
    """Fondo cuadrado con degradado vertical y esquinas redondeadas."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    gp = grad.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        for x in range(size):
            gp[x, y] = (r, g, b, 255)

    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    radius = int(size * 0.22)
    md.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def _draw_microphone(img: Image.Image, size: int):
    """Dibuja un microfono estilizado centrado, escalado al tamano del icono."""
    d = ImageDraw.Draw(img)
    cx = size / 2
    # Microfono: capsula redondeada en la mitad superior
    mic_w = size * 0.26
    mic_h = size * 0.34
    mic_x0 = cx - mic_w / 2
    mic_y0 = size * 0.22
    mic_x1 = cx + mic_w / 2
    mic_y1 = mic_y0 + mic_h
    radius = mic_w / 2

    # Cuerpo del microfono
    d.rounded_rectangle([mic_x0, mic_y0, mic_x1, mic_y1],
                        radius=radius, fill=MIC_BODY)
    # Brillo superior
    hl_w = mic_w * 0.28
    d.rounded_rectangle([mic_x0 + mic_w * 0.18, mic_y0 + mic_h * 0.10,
                         mic_x0 + mic_w * 0.18 + hl_w, mic_y0 + mic_h * 0.55],
                        radius=hl_w / 2, fill=MIC_HIGHLIGHT)

    # Arco del soporte del microfono
    arc_r = mic_w * 0.95
    arc_bbox = [cx - arc_r, mic_y1 - arc_r * 0.35,
                cx + arc_r, mic_y1 + arc_r * 1.25]
    lw = max(2, size * 0.028)
    d.arc(arc_bbox, start=20, end=160, fill=MIC_BODY, width=int(lw))

    # Pie vertical
    foot_w = max(2, size * 0.045)
    d.rectangle([cx - foot_w / 2, mic_y1 + mic_h * 0.12,
                 cx + foot_w / 2, size * 0.70], fill=MIC_BODY)
    # Base horizontal
    base_w = mic_w * 1.25
    d.rounded_rectangle([cx - base_w / 2, size * 0.66,
                         cx + base_w / 2, size * 0.72],
                        radius=foot_w / 2, fill=MIC_BODY)

    # Ondas de sonido a ambos lados
    wave_top = mic_y0 + mic_h * 0.30
    wave_bot = mic_y0 + mic_h * 0.80
    for side, direction in ((-1, 1), (1, -1)):
        base_x = cx + direction * (mic_w / 2 + size * 0.10)
        for i, amp in enumerate((0.22, 0.14, 0.07)):
            x0 = base_x + direction * (i * size * 0.075)
            d.arc([x0 - size * 0.06, wave_top - size * amp,
                   x0 + size * 0.06, wave_bot + size * amp],
                  start=110 if side == -1 else 70,
                  end=250 if side == -1 else 290,
                  fill=WAVE, width=max(2, int(size * 0.022)))


def _draw_accent_dot(img: Image.Image, size: int):
    """Punto de acento verde junto al microfono (senal de "activo/listo")."""
    d = ImageDraw.Draw(img)
    r = size * 0.045
    cx = size * 0.74
    cy = size * 0.30
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)


def generate_icon() -> None:
    os.makedirs(ASSETS_DIR, exist_ok=True)
    images = []
    for size in ICON_SIZES:
        img = _rounded_gradient_bg(size)
        _draw_microphone(img, size)
        if size >= 48:
            _draw_accent_dot(img, size)
        images.append(img)

    # PNG de alta resolucion (256)
    images[0].save(PNG_PATH, "PNG")
    # ICO multipaginal con todos los tamanoes
    images[0].save(ICO_PATH, format="ICO",
                   sizes=[(s, s) for s in ICON_SIZES])
    print(f"[OK] Icono ICO creado: {ICO_PATH}")
    print(f"[OK] Icono PNG creado: {PNG_PATH}")


if __name__ == "__main__":
    generate_icon()
