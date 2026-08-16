import os
import logging
import queue
import subprocess
import tempfile
from typing import List, Dict, Any, Optional
from faster_whisper import WhisperModel

# La configuración de logging la centraliza main.py; aquí solo obtenemos el logger.
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.mp3', '.wav'}

# Extensiones de video (requieren extracción de audio previa).
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov'}


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
    
    def __init__(self, model_size: str = "base", device: str = "cpu",
                 compute_type: str = "int8", cpu_threads: int = 4,
                 num_workers: int = 1):
        """
        Inicializa el modelo de Whisper.

        Args:
            model_size (str): Tamaño del modelo a cargar (por defecto 'base').
            device (str): Dispositivo de ejecución ('cpu' o 'cuda').
            compute_type (str): Tipo de cómputo para cuantización (por defecto 'int8').
            cpu_threads (int): Hilos para inferencia en CPU. 4 es óptimo para CPUs
                de 4 hilos lógicos; usar más no acelera y puede saturar la RAM.
            num_workers (int): Workers para decodificación de audio. 1 consume menos RAM.

        Raises:
            ModelLoadError: Si no se puede inicializar el modelo.
        """
        logger.info(f"Cargando modelo Whisper '{model_size}' en dispositivo '{device}' "
                    f"con compute_type '{compute_type}', cpu_threads={cpu_threads}, num_workers={num_workers}...")
        try:
            self.model = WhisperModel(
                model_size, device=device, compute_type=compute_type,
                cpu_threads=cpu_threads, num_workers=num_workers,
            )
            logger.info("Modelo Whisper cargado exitosamente.")
        except Exception as e:
            error_msg = f"Error al inicializar el modelo Whisper: {str(e)}"
            logger.critical(error_msg)
            raise ModelLoadError(error_msg) from e

    def transcribe_file(self, file_path: str, queue: "queue.Queue" = None,
                        beam_size: int = 1, vad_filter: bool = True,
                        language: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Transcribe un archivo de audio o video.

        Para acelerar la transcripción en CPU se aplican tres optimizaciones:
          1. Pre-extracción del audio a 16 kHz mono WAV con ffmpeg (los videos ya
             no se decodifican on-the-fly; ahorra RAM y evita fallos con codecs
             exóticos). El archivo temporal se borra al terminar.
          2. vad_filter=True: descarta los silencios (no se procesan partes mudas),
             lo que acelera sin perder calidad de lo hablado.
          3. beam_size=1 (greedy): ~2-3x más rápido que beam_size=5 con pérdida de
             calidad <1% WER. Subir a 5 si se prioriza máxima calidad sobre velocidad.

        Args:
            file_path (str): Ruta absoluta o relativa al archivo a transcribir.
            queue (queue.Queue, optional): Cola para enviar mensajes de progreso y estado.
            beam_size (int): Tamaño de haz. 1 = rápido (por defecto), 5 = máxima calidad.
            vad_filter (bool): Filtrar silencios con VAD (por defecto True).
            language (str, optional): Código de idioma (ej. 'es'). Si es None, se
                autodetecta. Especificarlo evita el overhead de detección.

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

        # Pre-extracción de audio si el archivo es un video. Esto reduce el uso de
        # RAM y evita fallos con contenedores/codecs exóticos. Si ffmpeg no está
        # disponible, se cede el archivo original a faster-whisper (que usa PyAV).
        audio_path = self._extract_audio_if_video(file_path, queue)

        try:
            transcribe_kwargs = {
                "beam_size": beam_size,
                "vad_filter": vad_filter,
            }
            if language:
                transcribe_kwargs["language"] = language
            segments, info = self.model.transcribe(audio_path, **transcribe_kwargs)

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
            logger.error(error_msg, exc_info=True)
            if queue:
                queue.put({"type": "error", "message": error_msg, "level": "error"})
            raise TranscriptionProcessError(error_msg) from e
        finally:
            self._cleanup_temp_audio(audio_path, file_path)

    def _extract_audio_if_video(self, file_path: str,
                                queue: "queue.Queue" = None) -> str:
        """Si file_path es un video, extrae el audio a un WAV temporal de 16 kHz
        mono PCM (formato óptimo para Whisper). Devuelve la ruta del WAV temporal,
        o la ruta original si no es video o si ffmpeg no está disponible.

        Extraer el audio antes reduce picos de RAM al decodificar contenedores
        comprimidos y evita errores con codecs exóticos. Sin pérdida de calidad:
        16 kHz mono ya es lo que consume el modelo internamente.
        """
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in VIDEO_EXTENSIONS:
            return file_path  # ya es audio: faster-whisper lo maneja directo

        if not _ffmpeg_available():
            logger.warning("ffmpeg no encontrado: se transcribe el video sin pre-extracción "
                           "(más lento y puede consumir más RAM). Instala ffmpeg para acelerar.")
            return file_path

        tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_wav.close()
        tmp_path = tmp_wav.name
        cmd = [
            "ffmpeg", "-y", "-i", file_path,
            "-vn",                 # descartar video
            "-acodec", "pcm_s16le",  # WAV PCM 16-bit
            "-ar", "16000",        # 16 kHz (lo que consume Whisper)
            "-ac", "1",            # mono
            tmp_path,
        ]
        logger.info(f"Extrayendo audio del video a WAV 16kHz mono: {tmp_path}")
        if queue:
            queue.put({"type": "status", "text": "Extrayendo audio del video...", "level": "processing"})
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_subprocess_creation_flags(),
            )
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", errors="replace").strip()
                logger.warning(f"ffmpeg falló al extraer audio ({err}). "
                               "Se transcribe el video original directamente.")
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return file_path
            logger.info("Audio extraído correctamente.")
            return tmp_path
        except FileNotFoundError:
            # ffmpeg desapareció entre la comprobación y la ejecución
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return file_path

    def _cleanup_temp_audio(self, audio_path: str, original_path: str) -> None:
        """Borra el WAV temporal si fue extraído (distinto del archivo original)."""
        if audio_path and os.path.abspath(audio_path) != os.path.abspath(original_path):
            try:
                os.unlink(audio_path)
                logger.info("Archivo de audio temporal eliminado.")
            except OSError:
                pass


def _ffmpeg_available() -> bool:
    """Comprueba si ffmpeg está en el PATH."""
    from shutil import which
    return which("ffmpeg") is not None


def _subprocess_creation_flags() -> int:
    """En Windows, oculta la ventana de consola de ffmpeg. En otros SO, 0."""
    if os.name == "nt":
        # CREATE_NO_WINDOW = 0x08000000
        return 0x08000000
    return 0


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
