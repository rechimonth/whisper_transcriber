import sys
import os

# Asegurar que el directorio raíz está en el path para importación del módulo core
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import MainWindow

def main():
    try:
        app = MainWindow()
        app.mainloop()
    except Exception as e:
        print(f"Error crítico al iniciar la aplicación: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
