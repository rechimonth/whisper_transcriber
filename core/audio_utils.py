"""Utilidades de audio con ffmpeg, compartidas por los transcriptores.

- extract_audio: video/audio -> MP3 mono 16 kHz (formato compacto para Groq)
  o WAV mono 16 kHz (para uso local). Devuelve (ruta, es_temporal).
- get_audio_duration: duración en segundos vía ffprobe.
- split_audio: trocea un archivo en N fragmentos de ~chunk_seconds.
- cleanup_temp_files: borra archivos temporales de forma segura.
"""
import os
import logging
import subprocess
import tempfile
from shutil import which
from typing import List, Tuple

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov"}


def ffmpeg_available() -> bool:
    return which("ffmpeg") is not None


def ffprobe_available() -> bool:
    return which("ffprobe") is not None


def _creation_flags() -> int:
    """En Windows oculta la consola de ffmpeg (CREATE_NO_WINDOW=0x08000000)."""
    return 0x08000000 if os.name == "nt" else 0


def _run_ffmpeg(cmd: List[str], desc: str) -> bool:
    """Ejecuta ffmpeg; devuelve True si OK. Loguea errores."""
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"ffmpeg falló ({desc}): {err}")
            return False
        return True
    except FileNotFoundError:
        logger.warning("ffmpeg no encontrado en el PATH.")
        return False


def extract_audio(file_path: str, fmt: str = "mp3") -> Tuple[str, bool]:
    """Convierte file_path a audio mono 16 kHz.

    Args:
        file_path: ruta de entrada (audio o video).
        fmt: 'mp3' (compacto, para Groq) o 'wav' (PCM, para uso local).

    Returns:
        (ruta_audio, es_temporal). Si la entrada ya es audio del formato
        adecuado, puede devolver la ruta original (es_temporal=False).
    """
    _, ext = os.path.splitext(file_path)
    is_video = ext.lower() in VIDEO_EXTENSIONS

    # Si ya es un archivo de audio y no se requiere conversion, devolver original.
    if not is_video and ext.lower() in {".mp3", ".wav"}:
        # Aun siendo mp3/wav, podria no ser 16kHz mono; para simplicidad se
        # acepta como esta cuando coincide con el formato pedido.
        return file_path, False

    if not ffmpeg_available():
        logger.warning("ffmpeg no disponible: se usa el archivo original.")
        return file_path, False

    tmp = tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False)
    tmp.close()
    cmd = ["ffmpeg", "-y", "-i", file_path, "-vn",
           "-ar", "16000", "-ac", "1", tmp.name]
    if fmt == "mp3":
        cmd += ["-acodec", "libmp3lame", "-b:a", "32k"]
    logger.info(f"Extrayendo audio a {fmt.upper()} 16kHz mono: {tmp.name}")
    if _run_ffmpeg(cmd, f"extract {fmt}"):
        return tmp.name, True
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return file_path, False


def get_audio_duration(file_path: str) -> float:
    """Duración del audio/video en segundos vía ffprobe."""
    if not ffprobe_available():
        logger.warning("ffprobe no disponible: no se puede medir la duración.")
        return 0.0
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
        if proc.returncode == 0:
            return float(proc.stdout.decode().strip() or 0.0)
    except (FileNotFoundError, ValueError) as e:
        logger.warning(f"ffprobe falló al medir duración: {e}")
    return 0.0


def split_audio(audio_path: str, duration: float, n_chunks: int,
                chunk_seconds: float,
                temp_files: List[str]) -> List[Tuple[str, float]]:
    """Trocea audio_path en fragmentos de ~chunk_seconds con ffmpeg (-ss/-to).

    Devuelve [(chunk_path, offset), ...]. Los paths creados se añaden a
    temp_files para su limpieza posterior.
    """
    chunks: List[Tuple[str, float]] = []
    for i in range(n_chunks):
        start = i * chunk_seconds
        if start >= duration:
            break
        end = min(start + chunk_seconds, duration)
        tmp = tempfile.NamedTemporaryFile(
            suffix=f"_part{i}.mp3", delete=False)
        tmp.close()
        cmd = ["ffmpeg", "-y", "-ss", str(start), "-to", str(end),
               "-i", audio_path, "-vn", "-acodec", "libmp3lame",
               "-b:a", "32k", "-ar", "16000", "-ac", "1", tmp.name]
        if _run_ffmpeg(cmd, f"split part {i+1}"):
            chunks.append((tmp.name, start))
            temp_files.append(tmp.name)
        else:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    if not chunks:
        # Fallback: enviar el archivo entero (puede exceder el limite, pero
        # mejor intentar que fallar sin mas).
        chunks.append((audio_path, 0.0))
    return chunks


def cleanup_temp_files(paths: List[str]) -> None:
    for p in paths:
        if not p:
            continue
        try:
            os.unlink(p)
        except OSError:
            pass
