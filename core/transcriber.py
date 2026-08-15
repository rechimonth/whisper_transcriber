import os
import logging
import queue
from typing import List, Dict, Any
from faster_whisper import WhisperModel

# Configuración básica de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.mp3', '.wav'}


class TranscriberError(Exception):
    """Clase base para excepciones en el transcriptor."""
    pass


class FileNotSupportedError(TranscriberError):
    """Excepción lanzada cuando el formato de archivo no está soportado."""
    pass


class ModelLoadError(TranscriberError):
    """Excepción lanzada cuando falla la carga del modelo Whisper."""
    pass


class TranscriptionProcessError(TranscriberError):
    """Excepción lanzada cuando ocurre un error durante el proceso de transcripción."""
    pass


def format_srt_timestamp(seconds: float) -> str:
    """
    Convierte segundos en formato float a formato de tiempo SRT (HH:MM:SS,mmm).
    
    Args:
        seconds (float): Tiempo en segundos.
        
    Returns:
        str: Timestamp formateado.
    """
    try:
        if seconds < 0:
            seconds = 0.0
        
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        milliseconds = int(round((seconds - int(seconds)) * 1000))
        
        # Corrección por redondeo
        if milliseconds >= 1000:
            secs += 1
            milliseconds -= 1000
            if secs >= 60:
                secs -= 60
                minutes += 1
                if minutes >= 60:
                    minutes -= 60
                    hours += 1
                    
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
    except Exception as e:
        logger.error(f"Error al formatear timestamp para {seconds} segundos: {str(e)}")
        return "00:00:00,000"


class WhisperTranscriber:
    """Clase para manejar la carga y transcripción de archivos usando faster-whisper."""
    
    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        """
        Inicializa el modelo de Whisper.
        
        Args:
            model_size (str): Tamaño del modelo a cargar (por defecto 'base').
            device (str): Dispositivo de ejecución ('cpu' o 'cuda').
            compute_type (str): Tipo de cómputo para cuantización (por defecto 'int8').
            
        Raises:
            ModelLoadError: Si no se puede inicializar el modelo.
        """
        logger.info(f"Cargando modelo Whisper '{model_size}' en dispositivo '{device}' con compute_type '{compute_type}'...")
        try:
            self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
            logger.info("Modelo Whisper cargado exitosamente.")
        except Exception as e:
            error_msg = f"Error al inicializar el modelo Whisper: {str(e)}"
            logger.critical(error_msg)
            raise ModelLoadError(error_msg) from e

    def transcribe_file(self, file_path: str, queue: "queue.Queue" = None) -> List[Dict[str, Any]]:
        """
        Transcribe un archivo de audio o video.
        
        Args:
            file_path (str): Ruta absoluta o relativa al archivo a transcribir.
            queue (queue.Queue, optional): Cola para enviar mensajes de progreso y estado.
        
        Returns:
            List[Dict[str, Any]]: Lista de segmentos, cada uno con start, end y text.
        
        Raises:
            FileNotFoundError: Si el archivo no existe.
            FileNotSupportedError: Si la extensión del archivo no está soportada.
            TranscriptionProcessError: Si ocurre un error al procesar el archivo.
        """
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in SUPPORTED_EXTENSIONS:
            error_msg = f"Extensión de archivo no soportada: {ext}. Formatos soportados: {', '.join(SUPPORTED_EXTENSIONS)}"
            logger.error(error_msg)
            raise FileNotSupportedError(error_msg)

        if not os.path.exists(file_path):
            error_msg = f"El archivo no existe: {file_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        # Notify start of transcription
        if queue:
            queue.put({"type": "status", "text": f"Iniciando transcripción de: {os.path.basename(file_path)}", "level": "processing"})
        logger.info(f"Iniciando transcripción de: {file_path}")
        try:
            # transcribe devuelve un generador de segmentos y un objeto de información
            segments, info = self.model.transcribe(file_path, beam_size=5)

            logger.info(f"Idioma detectado: {info.language} con probabilidad {info.language_probability:.2f}")
            if queue:
                queue.put({"type": "status", "text": f"Idioma detectado: {info.language} ({info.language_probability*100:.1f}%)", "level": "processing"})
            
            results = []
            total_duration = getattr(info, "duration", None)
            for idx, segment in enumerate(segments, start=1):
                seg_data = {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip(),
                }
                results.append(seg_data)
                # Calculate progress if possible
                if total_duration and total_duration > 0:
                    progress = min(segment.end / total_duration, 1.0)
                else:
                    progress = idx / (len(segments) or 1)
                if queue:
                    queue.put({"type": "segment", "text": seg_data["text"], "progress": progress})
            if queue:
                queue.put({"type": "done", "results": results, "level": "ok"})
            return results
        except Exception as e:
            error_msg = f"Ocurrió un error durante el procesamiento de transcripción: {str(e)}"
            logger.error(error_msg)
            if queue:
                queue.put({"type": "error", "message": error_msg, "level": "error"})
            raise TranscriptionProcessError(error_msg) from e


def export_to_txt(segments: List[Dict[str, Any]], output_path: str) -> None:
    """
    Exporta los segmentos transcritos a un archivo de texto plano (.txt).
    
    Args:
        segments (List[Dict[str, Any]]): Lista de segmentos transcritos.
        output_path (str): Ruta de destino para el archivo .txt.
        
    Raises:
        ValueError: Si los segmentos están vacíos o no tienen la estructura adecuada.
        IOError: Si ocurre un error de escritura.
    """
    if not segments:
        raise ValueError("No hay segmentos para exportar.")
        
    logger.info(f"Exportando resultados a formato TXT en: {output_path}")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            for segment in segments:
                f.write(f"{segment['text']}\n")
        logger.info("Exportación TXT completada con éxito.")
    except Exception as e:
        error_msg = f"Error al escribir el archivo TXT: {str(e)}"
        logger.error(error_msg)
        raise IOError(error_msg) from e


def export_to_srt(segments: List[Dict[str, Any]], output_path: str) -> None:
    """
    Exporta los segmentos transcritos a un archivo de subtítulos (.srt).
    
    Args:
        segments (List[Dict[str, Any]]): Lista de segmentos transcritos.
        output_path (str): Ruta de destino para el archivo .srt.
        
    Raises:
        ValueError: Si los segmentos están vacíos o no tienen la estructura adecuada.
        IOError: Si ocurre un error de escritura.
    """
    if not segments:
        raise ValueError("No hay segmentos para exportar.")
        
    logger.info(f"Exportando resultados a formato SRT en: {output_path}")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            for idx, segment in enumerate(segments, start=1):
                start_srt = format_srt_timestamp(segment['start'])
                end_srt = format_srt_timestamp(segment['end'])
                text = segment['text']
                
                f.write(f"{idx}\n")
                f.write(f"{start_srt} --> {end_srt}\n")
                f.write(f"{text}\n\n")
        logger.info("Exportación SRT completada con éxito.")
    except Exception as e:
        error_msg = f"Error al escribir el archivo SRT: {str(e)}"
        logger.error(error_msg)
        raise IOError(error_msg) from e
