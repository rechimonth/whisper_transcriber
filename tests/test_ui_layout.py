"""QA visual geometrico (anti-superposicion) para el layout CustomTkinter.

Instancia MainWindow, renderiza con update_idletasks() (sin mainloop) y valida
matematicamente sobre bounding boxes relativos (winfo_x/y/width/height):

- Sin coordenadas negativas.
- Contenido dentro de su contenedor padre.
- Cero intersecciones entre hermanos (widgets con el mismo padre). La
  contencion padre->hijo es legitima y no se cuenta como colision. Los fondos
  decorativos (flag _layout_bg) se excluyen a proposito: cubren a proposito.

Requiere display (usa el de la sesion). faster_whisper se sustituye por un
stub porque el layout no necesita el modelo real.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest
import customtkinter as ctk


def _install_faster_whisper_stub() -> None:
    if "faster_whisper" in sys.modules:
        return
    stub = types.ModuleType("faster_whisper")

    class WhisperModel:  # pragma: no cover - solo para import
        def __init__(self, *args, **kwargs):
            raise RuntimeError("stub sin modelo real")

    stub.WhisperModel = WhisperModel
    sys.modules["faster_whisper"] = stub


_install_faster_whisper_stub()

from ui.main_window import MainWindow  # noqa: E402


def _iter_widgets(root):
    """Recorre el arbol devolviendo (widget, padre)."""
    stack = [(child, root) for child in root.winfo_children()]
    while stack:
        widget, parent = stack.pop()
        yield widget, parent
        try:
            children = widget.winfo_children()
        except Exception:
            children = []
        stack.extend((c, widget) for c in children)


def _bbox(widget):
    return (
        widget.winfo_x(),
        widget.winfo_y(),
        widget.winfo_width(),
        widget.winfo_height(),
    )


def _is_testable(widget) -> bool:
    # Solo widgets CustomTkinter de nivel layout. Se excluyen a proposito:
    # - fondos decorativos (flag _layout_bg): cubren a proposito;
    # - internos Tk de los compuestos CTk (label/canvas/entry/text/scrollbar):
    #   el canvas pinta el fondo DETRAS del contenido por diseno.
    if not isinstance(widget, ctk.CTkBaseClass):
        return False
    if getattr(widget, "_layout_bg", False):
        return False
    _, _, w, h = _bbox(widget)
    return w >= 2 and h >= 2


def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


@pytest.fixture()
def window():
    win = MainWindow()
    # Triple pasada: idletasks + update (WM) + idletasks para geometria final.
    win.update_idletasks()
    win.update()
    win.update_idletasks()
    yield win
    try:
        logging.getLogger().removeHandler(win._log_handler)
    except Exception:
        pass
    sys.stdout = win._original_stdout
    sys.stderr = win._original_stderr
    try:
        win.destroy()
    except Exception:
        pass


def _collect(window):
    items = []
    for widget, parent in _iter_widgets(window):
        if _is_testable(widget):
            items.append({"widget": widget, "parent": parent, "box": _bbox(widget)})
    return items


def test_no_negative_coordinates(window):
    bad = [
        (str(i["widget"]), i["box"])
        for i in _collect(window)
        if i["box"][0] < 0 or i["box"][1] < 0
    ]
    assert not bad, f"Widgets con coords negativas: {bad}"


def test_widgets_inside_parent(window):
    bad = []
    for item in _collect(window):
        x, y, w, h = item["box"]
        parent = item["parent"]
        pw, ph = parent.winfo_width(), parent.winfo_height()
        if pw < 2 or ph < 2:
            continue
        if x < 0 or y < 0 or x + w > pw or y + h > ph:
            bad.append((str(item["widget"]), item["box"], (pw, ph)))
    assert not bad, f"Widgets fuera de su padre: {bad}"


def test_siblings_do_not_overlap(window):
    by_parent: dict[int, list] = {}
    for item in _collect(window):
        by_parent.setdefault(id(item["parent"]), []).append(item)
    collisions = []
    for items in by_parent.values():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if _overlaps(items[i]["box"], items[j]["box"]):
                    collisions.append(
                        (str(items[i]["widget"]), str(items[j]["widget"]))
                    )
    assert not collisions, f"Colisiones entre hermanos: {collisions}"
