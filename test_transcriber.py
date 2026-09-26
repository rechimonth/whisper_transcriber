import os
import wave
import struct
import math
import sys
from core.transcriber import (
    WhisperTranscriber,
    export_to_txt,
    export_to_srt,
    format_srt_timestamp,
    FileNotSupportedError,
    TranscriberError
)

def generate_test_wav(filepath: str, duration: float = 2.0, freq: float = 440.0, sample_rate: int = 16000) -> None:
    """Genera un archivo WAV de prueba con un tono sinusoidal simple."""
    print(f"Generando archivo WAV de prueba en: {filepath}")
    with wave.open(filepath, 'w') as wav_file:
        n_channels = 1
        sampwidth = 2  # 16-bit
        n_frames = int(duration * sample_rate)
        wav_file.setparams((n_channels, sampwidth, sample_rate, n_frames, 'NONE', 'not compressed'))
        
        for i in range(n_frames):
            # Onda senoidal simple
            value = int(32767 * math.sin(2 * math.pi * freq * (i / sample_rate)))
            wav_file.writeframes(struct.pack('<h', value))
    print("Archivo WAV generado con éxito.")

def test_transcription():
    # Rutas de prueba
    test_wav = "test_audio.wav"
    test_txt = "test_output.txt"
    test_srt = "test_output.srt"
    
    # 1. Generar archivo de prueba
    generate_test_wav(test_wav)
    
    # Asegurar limpieza al final
    try:
        # 2. Inicializar transcriptor (usaremos model_size="tiny" para que descargue rápido y valide en el test)
        print("\n--- Cargando modelo Tiny para pruebas ---")
        transcriber = WhisperTranscriber(model_size="tiny", device="cpu", compute_type="int8")
        
        # 3. Transcribir
        print("\n--- Ejecutando transcripción ---")
        segments = transcriber.transcribe_file(test_wav)
        print(f"Segmentos detectados: {segments}")
        
        # Si no detectó nada (silencio/tono), agregamos un segmento simulado si la lista está vacía
        # solo para probar la exportación.
        if not segments:
            print("No se detectó habla en el tono puro. Simulando un segmento para verificar exportación.")
            segments = [{"start": 0.0, "end": 2.0, "text": "[Tono sinusoidal de prueba]"}]
        
        # 4. Exportar a TXT y SRT
        print("\n--- Exportando resultados ---")
        export_to_txt(segments, test_txt)
        export_to_srt(segments, test_srt)
        
        # Validar existencia de archivos
        assert os.path.exists(test_txt), "Fallo: El archivo TXT no se creó."
        assert os.path.exists(test_srt), "Fallo: El archivo SRT no se creó."
        
        print("\nContenido del TXT generado:")
        with open(test_txt, "r", encoding="utf-8") as f:
            print(f.read())
            
        print("Contenido del SRT generado:")
        with open(test_srt, "r", encoding="utf-8") as f:
            print(f.read())
            
        # 5. Probar validación de formatos no soportados
        print("\n--- Probando validación de extensiones ---")
        try:
            transcriber.transcribe_file("test_invalid.txt")
            print("ERROR: Debería haber fallado con FileNotSupportedError")
            sys.exit(1)
        except FileNotSupportedError as e:
            print(f"Éxito: Se capturó la excepción esperada para extensión no soportada: {e}")
            
        # 6. Probar validación de archivo inexistente
        print("\n--- Probando validación de archivo inexistente ---")
        try:
            transcriber.transcribe_file("archivo_inexistente.wav")
            print("ERROR: Debería haber fallado con FileNotFoundError")
            sys.exit(1)
        except FileNotFoundError as e:
            print(f"Éxito: Se capturó la excepción esperada para archivo inexistente: {e}")
            
        print("\n¡TODAS LAS PRUEBAS DEL CORE FINALIZARON EXITOSAMENTE!")
        
    except Exception as e:
        print(f"\nOcurrió un error inesperado durante las pruebas: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Limpieza de archivos de prueba creados
        for file in [test_wav, test_txt, test_srt]:
            if os.path.exists(file):
                try:
                    os.remove(file)
                    print(f"Archivo de prueba eliminado: {file}")
                except Exception as ex:
                    print(f"No se pudo eliminar {file}: {ex}")

if __name__ == "__main__":
    test_transcription()
