"""Cliente robusto para transcripcion mediante la API de Groq.

El modulo es reutilizable tanto por el cliente de escritorio como por el
backend. La API key nunca esta embebida: puede inyectarse mediante el
constructor o leerse del entorno en procesos que tengan permiso para usarla.

Caracteristicas:
- validacion de tamano y duracion antes de procesar;
- limite de bytes configurable por entorno;
- reintentos para 429, 408, 500, 502, 503 y timeouts;
- backoff exponencial con soporte para Retry-After;
- hasta 3 workers concurrentes;
- checkpoint incremental por fragmento para poder retomar;
- timestamps absolutos y resultado ordenado cronologicamente.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from core.audio_utils import (
    cleanup_temp_files,
    extract_audio,
    get_audio_duration,
    split_audio,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "whisper-large-v3-turbo"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_CHUNK_SECONDS = 600
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_BASE_DELAY = 2.0
DEFAULT_MAX_BACKOFF = 60.0
DEFAULT_MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_DURATION_SECONDS = 12 * 60 * 60


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Valor invalido para %s=%r; usando %s.", name, raw, default)
        return default
    return max(value, minimum)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Valor invalido para %s=%r; usando %s.", name, raw, default)
        return default
    return max(value, minimum)


GROQ_FREE_MAX_BYTES = _env_int("GROQ_FREE_MAX_BYTES", DEFAULT_MAX_BYTES)
CHUNK_SECONDS = _env_int("GROQ_CHUNK_SECONDS", DEFAULT_CHUNK_SECONDS)
MAX_RETRIES = _env_int("GROQ_MAX_RETRIES", DEFAULT_MAX_RETRIES)
RETRY_BASE_DELAY = _env_float("GROQ_RETRY_BASE_DELAY", DEFAULT_RETRY_BASE_DELAY)
MAX_BACKOFF_DELAY = _env_float("GROQ_MAX_BACKOFF_SECONDS", DEFAULT_MAX_BACKOFF)
MAX_WORKERS = min(_env_int("GROQ_MAX_WORKERS", 3), 3)
MAX_INPUT_FILE_BYTES = _env_int(
    "GROQ_MAX_INPUT_BYTES", DEFAULT_MAX_INPUT_BYTES
)
MAX_INPUT_DURATION_SECONDS = _env_int(
    "GROQ_MAX_DURATION_SECONDS", DEFAULT_MAX_DURATION_SECONDS
)
CHECKPOINT_DIR = os.getenv("TRANSCRIBER_CHECKPOINT_DIR", tempfile.gettempdir())


class GroqTranscriberError(Exception):
    """Error base del transcriptor Groq."""


class GroqNotConfiguredError(GroqTranscriberError):
    """No hay API key de Groq configurada."""


class GroqValidationError(GroqTranscriberError):
    """Entrada fuera de los limites configurados."""


class GroqTranscriber:
    """Transcriptor Groq con procesamiento concurrente y checkpointing."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        max_workers: Optional[int] = None,
    ) -> None:
        resolved_key = (api_key or os.getenv("GROQ_API_KEY", "")).strip()
        if not resolved_key:
            raise GroqNotConfiguredError(
                "No hay API key de Groq configurada."
            )

        try:
            from groq import Groq
        except ImportError as exc:
            raise GroqNotConfiguredError(
                "El paquete 'groq' no esta instalado. Ejecuta: pip install groq"
            ) from exc

        self.client = Groq(api_key=resolved_key)
        self.model = model
        self.max_workers = min(
            max(int(max_workers or MAX_WORKERS), 1),
            3,
        )
        logger.info(
            "GroqTranscriber listo: modelo=%s workers=%s max_bytes=%s",
            model,
            self.max_workers,
            GROQ_FREE_MAX_BYTES,
        )

    def transcribe_file(
        self,
        file_path: str,
        queue=None,
        language: Optional[str] = None,
        checkpoint_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Transcribe un archivo y conserva progreso parcial en disco."""
        self._validate_source(file_path)

        if queue:
            queue.put(
                {
                    "type": "status",
                    "text": "Preparando audio para Groq...",
                    "level": "processing",
                }
            )

        logger.info("Transcribiendo con Groq: %s", file_path)
        original_size = os.path.getsize(file_path)
        source_duration = get_audio_duration(file_path)

        audio_path, own_audio = extract_audio(file_path)
        temp_files = [audio_path] if own_audio else []

        try:
            duration = source_duration or get_audio_duration(audio_path)
            self._validate_duration(duration)

            signature = self._source_signature(
                file_path=file_path,
                size=original_size,
                duration=duration,
                checkpoint_id=checkpoint_id,
            )
            checkpoint_path = self._checkpoint_path(signature)

            chunks = self._prepare_chunks(audio_path, duration, temp_files)
            chunk_count = len(chunks)
            results_by_chunk = self._load_checkpoint(
                checkpoint_path,
                signature=signature,
                duration=duration,
                language=language,
                chunk_count=chunk_count,
            )

            pending = [
                (index, chunk_path, offset)
                for index, (chunk_path, offset) in enumerate(chunks)
                if index not in results_by_chunk
            ]

            if not pending:
                results = self._merge_results(results_by_chunk)
                self._clear_checkpoint(checkpoint_path)
                if queue:
                    queue.put(
                        {"type": "done", "results": results, "level": "ok"}
                    )
                return results

            if queue:
                completed = len(results_by_chunk)
                queue.put(
                    {
                        "type": "status",
                        "text": (
                            f"Procesando {len(pending)} fragmentos "
                            f"({completed}/{chunk_count} ya recuperados) con "
                            f"{min(self.max_workers, len(pending))} workers..."
                        ),
                        "level": "processing",
                    }
                )

            failures: List[BaseException] = []
            with ThreadPoolExecutor(
                max_workers=min(self.max_workers, len(pending))
            ) as executor:
                future_map = {
                    executor.submit(
                        self._transcribe_one,
                        chunk_path,
                        offset,
                        language,
                        index + 1,
                        chunk_count,
                        queue,
                    ): index
                    for index, chunk_path, offset in pending
                }

                for future in as_completed(future_map):
                    index = future_map[future]
                    try:
                        segments = future.result()
                    except BaseException as exc:
                        failures.append(exc)
                        logger.error(
                            "Fallo en fragmento %s/%s: %s",
                            index + 1,
                            chunk_count,
                            exc,
                            exc_info=True,
                        )
                        continue

                    results_by_chunk[index] = segments
                    self._save_checkpoint(
                        checkpoint_path,
                        signature=signature,
                        duration=duration,
                        language=language,
                        chunk_count=chunk_count,
                        results_by_chunk=results_by_chunk,
                    )

                    if queue:
                        progress = min(
                            len(results_by_chunk) / chunk_count,
                            1.0,
                        )
                        queue.put(
                            {
                                "type": "segment",
                                "text": (
                                    segments[-1]["text"] if segments else ""
                                ),
                                "progress": progress,
                                "chunk": index + 1,
                                "total_chunks": chunk_count,
                            }
                        )

            if failures:
                if queue:
                    queue.put(
                        {
                            "type": "status",
                            "text": (
                                "El proceso se interrumpio. El checkpoint "
                                "quedo guardado para retomar los fragmentos "
                                "completados."
                            ),
                            "level": "error",
                        }
                    )
                raise GroqTranscriberError(
                    f"Fallaron {len(failures)} fragmento(s). "
                    f"El progreso parcial fue guardado en {checkpoint_path}"
                ) from failures[0]

            results = self._merge_results(results_by_chunk)
            self._clear_checkpoint(checkpoint_path)

            if queue:
                queue.put(
                    {"type": "done", "results": results, "level": "ok"}
                )
            return results
        finally:
            cleanup_temp_files(temp_files)

    def _validate_source(self, file_path: str) -> None:
        if not file_path:
            raise GroqValidationError("No se especifico ningun archivo.")
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"El archivo no existe: {file_path}")

        size = os.path.getsize(file_path)
        if size <= 0:
            raise GroqValidationError("El archivo esta vacio.")
        if size > MAX_INPUT_FILE_BYTES:
            raise GroqValidationError(
                f"El archivo supera el limite configurado de "
                f"{MAX_INPUT_FILE_BYTES / (1024 * 1024):.0f} MB."
            )

    @staticmethod
    def _validate_duration(duration: float) -> None:
        if duration <= 0:
            raise GroqValidationError(
                "No se pudo determinar la duracion del audio. "
                "Comprueba que ffprobe este instalado."
            )
        if duration > MAX_INPUT_DURATION_SECONDS:
            raise GroqValidationError(
                "La duracion supera el maximo configurado de "
                f"{MAX_INPUT_DURATION_SECONDS / 3600:.1f} horas."
            )

    def _prepare_chunks(
        self,
        audio_path: str,
        duration: float,
        temp_files: List[str],
    ) -> List[tuple[str, float]]:
        size = os.path.getsize(audio_path)
        needs_split = (
            size > GROQ_FREE_MAX_BYTES or duration > CHUNK_SECONDS
        )
        if not needs_split:
            return [(audio_path, 0.0)]

        by_duration = math.ceil(duration / CHUNK_SECONDS)
        by_size = math.ceil(size / max(GROQ_FREE_MAX_BYTES, 1))
        n_chunks = max(by_duration, by_size, 2)

        logger.info(
            "Troceando audio %.1fs (%s bytes) en %s fragmentos.",
            duration,
            size,
            n_chunks,
        )
        chunks = split_audio(
            audio_path,
            duration,
            n_chunks,
            CHUNK_SECONDS,
            temp_files,
        )

        if not chunks:
            raise GroqTranscriberError(
                "No fue posible generar fragmentos de audio con ffmpeg."
            )

        oversized = [
            path
            for path, _ in chunks
            if os.path.getsize(path) > GROQ_FREE_MAX_BYTES
        ]
        if oversized and len(chunks) == 1:
            raise GroqValidationError(
                "El fragmento generado sigue superando GROQ_FREE_MAX_BYTES."
            )
        return chunks

    def _transcribe_one(
        self,
        chunk_path: str,
        offset: float,
        language: Optional[str],
        chunk_index: int,
        total_chunks: int,
        queue=None,
    ) -> List[Dict[str, Any]]:
        """Envio a Groq con reintentos para errores transitorios."""
        last_exc: Optional[BaseException] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Reabrir el archivo en cada intento evita reutilizar un
                # file-pointer que haya quedado consumido por una solicitud fallida.
                with open(chunk_path, "rb") as audio_file:
                    kwargs: Dict[str, Any] = {
                        "model": self.model,
                        "file": audio_file,
                        "response_format": "verbose_json",
                    }
                    if language:
                        kwargs["language"] = language

                    response = self.client.audio.transcriptions.create(
                        **kwargs
                    )
                return self._response_to_segments(response, offset)
            except Exception as exc:
                last_exc = exc
                retry_after = self._extract_retry_after(exc)
                retryable = self._is_retryable(exc)
                if not retryable or attempt >= MAX_RETRIES:
                    msg = (
                        f"Error de Groq en fragmento {chunk_index}/"
                        f"{total_chunks}: {exc}"
                    )
                    raise GroqTranscriberError(msg) from exc

                delay = (
                    retry_after
                    if retry_after is not None
                    else min(
                        RETRY_BASE_DELAY * (2 ** (attempt - 1)),
                        MAX_BACKOFF_DELAY,
                    )
                )
                delay = max(0.0, min(delay, MAX_BACKOFF_DELAY))
                logger.warning(
                    "Reintento Groq fragmento %s/%s por error transitorio "
                    "(intento %s/%s); espera %.1fs.",
                    chunk_index,
                    total_chunks,
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                )
                if queue:
                    queue.put(
                        {
                            "type": "status",
                            "text": (
                                f"Groq temporalmente no disponible en "
                                f"fragmento {chunk_index}/{total_chunks}. "
                                f"Reintento {attempt + 1}/{MAX_RETRIES} "
                                f"en {int(delay)}s..."
                            ),
                            "level": "processing",
                        }
                    )
                time.sleep(delay)

        raise GroqTranscriberError(
            f"No fue posible transcribir el fragmento {chunk_index}."
        ) from last_exc

    @staticmethod
    def _response_to_segments(
        response: Any, offset: float
    ) -> List[Dict[str, Any]]:
        segments: List[Dict[str, Any]] = []
        raw_segments = getattr(response, "segments", None) or []

        for segment in raw_segments:
            start = float(getattr(segment, "start", 0.0)) + offset
            end = float(getattr(segment, "end", 0.0)) + offset
            text = str(getattr(segment, "text", "") or "").strip()
            if text:
                segments.append(
                    {"start": start, "end": end, "text": text}
                )

        if not segments:
            text = str(getattr(response, "text", "") or "").strip()
            if text:
                segments.append(
                    {"start": offset, "end": offset, "text": text}
                )
        return segments

    @staticmethod
    def _status_code(exc: BaseException) -> Optional[int]:
        for attr in ("status_code", "status"):
            value = getattr(exc, attr, None)
            if isinstance(value, int):
                return value
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
        return value if isinstance(value, int) else None

    @classmethod
    def _is_timeout(cls, exc: BaseException) -> bool:
        name = exc.__class__.__name__.lower()
        module = exc.__class__.__module__.lower()
        return (
            isinstance(exc, TimeoutError)
            or "timeout" in name
            or "timeout" in module
        )

    @classmethod
    def _is_retryable(cls, exc: BaseException) -> bool:
        status = cls._status_code(exc)
        if status in {408, 429, 500, 502, 503}:
            return True
        return cls._is_timeout(exc)

    @staticmethod
    def _extract_retry_after(exc: BaseException) -> Optional[float]:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value is None:
            return None
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _source_signature(
        file_path: str,
        size: int,
        duration: float,
        checkpoint_id: Optional[str] = None,
    ) -> str:
        if checkpoint_id:
            material = checkpoint_id
        else:
            stat = os.stat(file_path)
            material = (
                f"{os.path.abspath(file_path)}|{size}|"
                f"{stat.st_mtime_ns}|{duration:.6f}"
            )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _checkpoint_path(signature: str) -> str:
        safe_dir = CHECKPOINT_DIR
        os.makedirs(safe_dir, exist_ok=True)
        return os.path.join(
            safe_dir,
            f"temp_transcript_{signature}.json",
        )

    @staticmethod
    def _load_checkpoint(
        path: str,
        signature: str,
        duration: float,
        language: Optional[str],
        chunk_count: int,
    ) -> Dict[int, List[Dict[str, Any]]]:
        if not os.path.isfile(path):
            return {}

        try:
            with open(path, "r", encoding="utf-8") as file:
                payload = json.load(file)

            if (
                payload.get("version") != 1
                or payload.get("signature") != signature
                or abs(float(payload.get("duration", 0.0)) - duration) > 1.0
                or payload.get("language") != language
                or int(payload.get("chunk_count", -1)) != chunk_count
            ):
                return {}

            raw_results = payload.get("results", {})
            return {
                int(index): list(segments)
                for index, segments in raw_results.items()
                if isinstance(segments, list)
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.warning("Checkpoint invalido o corrupto: %s", path)
            return {}

    @staticmethod
    def _save_checkpoint(
        path: str,
        signature: str,
        duration: float,
        language: Optional[str],
        chunk_count: int,
        results_by_chunk: Dict[int, List[Dict[str, Any]]],
    ) -> None:
        payload = {
            "version": 1,
            "signature": signature,
            "duration": duration,
            "language": language,
            "chunk_count": chunk_count,
            "updated_at": time.time(),
            "results": {
                str(index): segments
                for index, segments in results_by_chunk.items()
            },
        }

        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)

    @staticmethod
    def _merge_results(
        results_by_chunk: Dict[int, List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        for index in sorted(results_by_chunk):
            merged.extend(results_by_chunk[index])
        merged.sort(key=lambda item: (float(item["start"]), float(item["end"])))
        return merged

    @staticmethod
    def _clear_checkpoint(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("No se pudo borrar checkpoint %s: %s", path, exc)


def is_groq_available() -> bool:
    """Disponible para procesos que deliberadamente usan la API desde su entorno."""
    if not os.getenv("GROQ_API_KEY", "").strip():
        return False
    try:
        import groq  # noqa: F401
        return True
    except ImportError:
        return False
