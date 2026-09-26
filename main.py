import sys
import os
import logging

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)


def _bootstrap_streams():
    """pythonw.exe no tiene consola: sys.stdout/stderr pueden ser None."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _configure_logging():
    """Configura app.log y consola sin duplicar handlers."""
    log_file = os.path.join(APP_DIR, "app.log")
    fmt = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
        except OSError:
            pass
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(fmt)
        root.addHandler(console_handler)


def _load_client_env():
    """Carga solo configuracion local; una Groq API key nunca se usa en el cliente."""
    env_path = os.path.join(APP_DIR, ".env")
    if not os.path.exists(env_path):
        return

    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        try:
            with open(env_path, encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if key.strip() == "TRANSCRIBER_BACKEND_URL":
                        os.environ.setdefault(key.strip(), value.strip())
                    elif key.strip() == "TRANSCRIBER_AUTH_TOKEN":
                        os.environ.setdefault(key.strip(), value.strip())
        except OSError:
            pass

    # Defense in depth: the desktop process must never carry the Groq secret.
    os.environ.pop("GROQ_API_KEY", None)
    os.environ.pop("GROQ_API_KEY_SERVER", None)


_bootstrap_streams()
_configure_logging()
_load_client_env()
logger = logging.getLogger(__name__)

from ui.main_window import MainWindow


def main():
    try:
        app = MainWindow()
        app.mainloop()
    except Exception:
        logger.critical("Error critico al iniciar la aplicacion", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
