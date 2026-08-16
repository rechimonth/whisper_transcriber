import sys
import os
import logging

# Asegurar que el directorio raíz está en el path para importación del módulo core
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)


def _bootstrap_streams():
    """pythonw.exe no tiene consola: sys.stdout/stderr son None, lo que rompe
    logging y los hooks de excepciones. Sustituirlos por streams validos."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _configure_logging():
    """Configuracion de logging robusta: archivo app.log + consola.
    Se llama antes de importar la UI para que los logs de faster_whisper y del
    worker se capturen siempre, incluso bajo pythonw."""
    log_file = os.path.join(APP_DIR, "app.log")
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Evitar duplicar handlers si se reimporta
    if not root.handlers:
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception:
            pass  # sin permiso de escritura: no es fatal
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)


_bootstrap_streams()
_configure_logging()
logger = logging.getLogger(__name__)

from ui.main_window import MainWindow


def main():
    try:
        app = MainWindow()
        app.mainloop()
    except Exception as e:
        logger.critical("Error critico al iniciar la aplicacion", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
