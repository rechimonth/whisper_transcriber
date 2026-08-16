"""Transcripción mediante la API de Groq (Whisper Large v3 Turbo).

Groq ejecuta Whisper en su hardware LPU: mucho más rápido que la CPU local y
con mejor calidad. Tier gratuito generoso (ver AGENTS.md).

Flujo:
  1. Si la entrada es un video, se extrae el audio a MP3 mono 16 kHz con ffmpeg
     (formato compacto: caben más minutos en el límite de 25 MB por archivo).
  2. Si el archivo supera 25 MB, se trocea en fragmentos de ~10 minutos con
     ffmpeg (-ss/-to) y se envían por separado a la API.
  3. Los segmentos devueltos por cada fragmento se ajustan sumando el offset de
     tiempo del corte, y se concatenan en una única lista de segmentos.
  4. El formato de salida es el mismo que core.transcriber.WhisperTranscriber:
     [{"start": float, "end": float, "text": str}, ...].
"""
import os
import logging
import subprocess
import tempfile
from typing import List, Dict, Any, Optional

from core.audio_utils import (
    extract_audio,
    get_audio_duration,
    split_audio,
    cleanup_temp_files,
)

logger = logging.getLogger(__name__)

# Límite de tamaño del tier gratuito de Groq (25 MB por archivo).
GROQ_FREE_MAX_BYTES = 25 * 1024 * 1024
# Modelo: turbo es más rápido y más barato, multilingüe, calidad suficiente.
DEFAULT_MODEL = "whisper-large-v3-turbo"
# Duración objetivo de cada fragmento (segundos). 10 min de MP3 mono 16kHz
# ocupa ~9 MB, con margen suficiente por debajo de 25 MB.
CHUNK_SECONDS = 600


class GroqTranscriberError(Exception):
    """Error base del transcriptor Groq."""
    pass


class GroqNotConfiguredError(GroqTranscriberError):
    """No hay API key de Groq configurada."""
    pass


class GroqTranscriber:
    """Transcriptor que usa la API de Groq (Whisper Large v3 Turbo)."""

    def __init__(self, model: str = DEFAULT_MODEL):
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            raise GroqNotConfiguredError(
                "No hay API key de Groq configurada. Crea un archivo .env con "
                "GROQ_API_KEY=tu_key (ver .env.example)."
            )
        try:
            from groq import Groq
        except ImportError as e:
            raise GroqNotConfiguredError(
                "El paquete 'groq' no está instalado. Ejecuta: pip install groq"
            ) from e
        self.client = Groq(api_key=api_key)
        self.model = model
        logger.info(f"GroqTranscriber listo con modelo '{model}'.")

    def transcribe_file(self, file_path: str, queue=None,
                        language: Optional[str] = None) -> List[Dict[str, Any]]:
        """Transcribe un archivo de audio/video mediante Groq.

        Args:
            file_path: ruta al archivo (audio o video).
            queue: cola opcional para progreso/estado (mismo protocolo que el
                transcriptor local).
            language: código de idioma opcional (ej. 'es').

        Returns:
            Lista de segmentos {start, end, text} con timestamps absolutos.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"El archivo no existe: {file_path}")

        if queue:
            queue.put({"type": "status", "text": "Preparando audio para Groq...",
                       "level": "processing"})
        logger.info(f"Transcribiendo con Groq: {file_path}")

        # 1) Asegurar audio en formato compacto (MP3 mono 16kHz).
        audio_path, own_audio = extract_audio(file_path)
        temp_files = [audio_path] if own_audio else []

        try:
            duration = get_audio_duration(audio_path)
            if duration <= 0:
                raise GroqTranscriberError(
                    "No se pudo determinar la duración del audio.")

            # 2) Trocear si el archivo supera el límite de Groq.
            chunks = self._prepare_chunks(audio_path, duration, temp_files)

            results: List[Dict[str, Any]] = []
            for i, (chunk_path, offset) in enumerate(chunks):
                if queue:
                    queue.put({
                        "type": "status",
                        "text": f"Transcribiendo fragmento {i+1}/{len(chunks)}...",
                        "level": "processing",
                    })
                logger.info(f"Fragmento {i+1}/{len(chunks)} (offset {offset:.1f}s)")
                segs = self._transcribe_one(chunk_path, offset, language)
                results.extend(segs)
                if queue:
                    progress = min((i + 1) / len(chunks), 1.0)
                    queue.put({"type": "segment",
                               "text": segs[-1]["text"] if segs else "",
                               "progress": progress})

            if queue:
                queue.put({"type": "done", "results": results, "level": "ok"})
            return results
        finally:
            cleanup_temp_files(temp_files)

    def _prepare_chunks(self, audio_path: str, duration: float,
                        temp_files: List[str]) -> List[tuple]:
        """Devuelve [(chunk_path, offset_seconds), ...]. Si el archivo cabe en
        el límite, devuelve una sola entrada con offset 0."""
        size = os.path.getsize(audio_path)
        if size <= GROQ_FREE_MAX_BYTES and duration <= CHUNK_SECONDS:
            return [(audio_path, 0.0)]

        n_chunks = max(int(duration // CHUNK_SECONDS) + 1, 2)
        logger.info(f"Audio de {duration:.0f}s ({size/1e6:.1f} MB): troceando en "
                    f"{n_chunks} fragmentos.")
        chunks = split_audio(audio_path, duration, n_chunks, CHUNK_SECONDS,
                             temp_files)
        return chunks

    def _transcribe_one(self, chunk_path: str, offset: float,
                        language: Optional[str]) -> List[Dict[str, Any]]:
        """Envía un fragmento a la API de Groq y devuelve sus segmentos con
        timestamps ajustados al offset del corte."""
        with open(chunk_path, "rb") as f:
            kwargs = {
                "model": self.model,
                "file": f,
                "response_format": "verbose_json",
            }
            if language:
                kwargs["language"] = language
            try:
                resp = self.client.audio.transcriptions.create(**kwargs)
            except Exception as e:
                msg = f"Error de la API de Groq: {e}"
                logger.error(msg, exc_info=True)
                raise GroqTranscriberError(msg) from e

        segments = []
        # response_format verbose_json incluye "segments" con start/end/text.
        raw_segments = getattr(resp, "segments", None) or []
        for seg in raw_segments:
            start = float(getattr(seg, "start", 0.0)) + offset
            end = float(getattr(seg, "end", 0.0)) + offset
            text = (getattr(seg, "text", "") or "").strip()
            if text:
                segments.append({"start": start, "end": end, "text": text})

        # Si la API no devolvió segmentos estructurados, usar el texto plano.
        if not segments:
            text = (getattr(resp, "text", "") or "").strip()
            if text:
                segments.append({"start": offset, "end": offset, "text": text})
        return segments


def is_groq_available() -> bool:
    """True si hay API key configurada Y el paquete groq está instalado."""
    if not os.environ.get("GROQ_API_KEY", "").strip():
        return False
    try:
        import groq  # noqa: F401
        return True
    except ImportError:
        return False
