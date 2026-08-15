import os
import sys
import subprocess
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BuildManager")

def compile_application():
    logger.info("Iniciando el proceso de empaquetado...")
    
    # 1. Determinar el directorio raíz del proyecto
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root_dir)
    logger.info(f"Directorio raíz de trabajo establecido en: {root_dir}")
    
    # 2. Localizar CustomTkinter dinámicamente para sus assets
    try:
        import customtkinter
        ctk_dir = os.path.dirname(customtkinter.__file__)
        ctk_assets = os.path.join(ctk_dir, "assets")
        if not os.path.exists(ctk_assets):
            raise FileNotFoundError(f"No se encontró el directorio de assets de customtkinter en: {ctk_assets}")
        logger.info(f"Assets de CustomTkinter localizados en: {ctk_assets}")
    except ImportError:
        logger.error("CustomTkinter no está instalado en el entorno actual de Python.")
        sys.exit(1)
        
    # 3. Construir argumento --add-data para CustomTkinter
    # En Windows, el separador es ';'
    add_data_value = f"{ctk_assets}{os.path.pathsep}customtkinter{os.sep}assets"
    logger.info(f"Argumento --add-data configurado: '{add_data_value}'")
    
    # 4. Configurar comando de PyInstaller
    # Usamos --onedir para mejorar sustancialmente el tiempo de inicio (evita descompresión de PyTorch/ctranslate2 en temp).
    # Colectamos todo el contenido de faster_whisper, ctranslate2 y customtkinter para evitar DLLs o archivos faltantes.
    cmd = [
        "pyinstaller",
        "--name=TranscriptorVideoIA",
        "--onedir",
        "--windowed",
        f"--add-data={add_data_value}",
        "--collect-all=faster_whisper",
        "--collect-all=ctranslate2",
        "--collect-all=customtkinter",
        "--clean",
        "--noconfirm",
        "main.py"
    ]
    
    logger.info(f"Ejecutando comando PyInstaller:\n{' '.join(cmd)}")
    
    # 5. Ejecutar PyInstaller en el venv
    try:
        # Buscamos el comando pyinstaller en el venv (Windows)
        pyinstaller_bin = os.path.join("venv", "Scripts", "pyinstaller.exe")
        if os.path.exists(pyinstaller_bin):
            cmd[0] = pyinstaller_bin
            logger.info(f"Usando binario de PyInstaller del venv: {pyinstaller_bin}")
        else:
            logger.warning("No se encontró pyinstaller.exe en venv/Scripts/. Se intentará usar desde el PATH global.")
            
        result = subprocess.run(cmd, capture_output=False, check=True)
        logger.info("¡Proceso de empaquetado de PyInstaller completado con éxito!")
        logger.info("El ejecutable y sus dependencias se encuentran en: dist/TranscriptorVideoIA")
        # 6. Crear acceso directo en el escritorio
        shortcut_script = os.path.join(root_dir, 'build', 'create_shortcut.py')
        if os.path.isfile(shortcut_script):
            try:
                subprocess.run([sys.executable, shortcut_script], check=True)
                logger.info("Acceso directo creado exitosamente en el escritorio.")
            except subprocess.CalledProcessError as e:
                logger.error(f"Error al crear el acceso directo: {e}")
        else:
            logger.warning(f"Script de creación de acceso directo no encontrado en {shortcut_script}")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Error al ejecutar PyInstaller (Código de salida {e.returncode}).")
        sys.exit(e.returncode)
    except Exception as e:
        logger.error(f"Ocurrió un error inesperado durante la compilación: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    compile_application()
