"""Proxy del backend hacia Groq.

El cliente de escritorio nunca recibe la API key. El servidor mide la
duracion, reserva los creditos, transcribe y reintegra el saldo si el servicio
externo falla.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from backend.auth import AuthenticatedUser, current_user_dependency
from backend.credits import (
    InsufficientCreditsError,
    credit_store,
    credits_for_duration,
)
from core.audio_utils import get_audio_duration
from core.groq_transcriber import (
    GroqNotConfiguredError,
    GroqTranscriber,
    GroqTranscriberError,
    GroqValidationError,
)

router = APIRouter(tags=["transcription"])

SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".m4a"
}
MAX_UPLOAD_BYTES = int(
    os.getenv("BACKEND_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024))
)
SERVER_TMP_DIR = Path(
    os.getenv("BACKEND_TMP_DIR", tempfile.gettempdir())
) / "transcriptorvideoia"


async def _save_upload(upload: UploadFile, destination: Path) -> str:
    total = 0
    digest = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="El archivo supera el limite maximo configurado.",
                )
            digest.update(chunk)
            output.write(chunk)

    return digest.hexdigest()


def _segments_to_text(segments: list[dict]) -> str:
    return "\n".join(
        str(segment.get("text", "")).strip()
        for segment in segments
        if str(segment.get("text", "")).strip()
    )


@router.get("/credits")
def get_credits(user: AuthenticatedUser = Depends(current_user_dependency)) -> dict:
    return {
        "user_id": user.user_id,
        "credits": credit_store.get_balance(user.user_id),
    }


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    user: AuthenticatedUser = Depends(current_user_dependency),
) -> dict:
    filename = Path(audio.filename or "upload.bin")
    if filename.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                "Formato no soportado. Usa: "
                + ", ".join(sorted(SUPPORTED_EXTENSIONS))
            ),
        )

    server_key = os.getenv("GROQ_API_KEY_SERVER", "").strip()
    if not server_key:
        raise HTTPException(
            status_code=503,
            detail="El servidor no tiene GROQ_API_KEY_SERVER configurada.",
        )

    path = SERVER_TMP_DIR / f"{uuid4().hex}_{filename.name}"
    reserved_credits = 0

    try:
        file_hash = await _save_upload(audio, path)
        duration = get_audio_duration(str(path))
        if duration <= 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No se pudo determinar la duracion del audio. "
                    "Comprueba que ffprobe este instalado."
                ),
            )

        required = credits_for_duration(duration)
        try:
            remaining = credit_store.reserve(user.user_id, required)
            reserved_credits = required
        except InsufficientCreditsError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

        try:
            transcriber = GroqTranscriber(
                api_key=server_key,
                max_workers=min(int(os.getenv("GROQ_MAX_WORKERS", "3")), 3),
            )
            segments = transcriber.transcribe_file(
                str(path),
                language=None,
                checkpoint_id=f"{user.user_id}-{file_hash[:20]}",
            )
        except (
            GroqNotConfiguredError,
            GroqValidationError,
            GroqTranscriberError,
        ):
            if reserved_credits:
                remaining = credit_store.refund(user.user_id, reserved_credits)
                reserved_credits = 0
            raise
        except Exception:
            if reserved_credits:
                remaining = credit_store.refund(user.user_id, reserved_credits)
                reserved_credits = 0
            raise

        reserved_credits = 0
        return {
            "text": _segments_to_text(segments),
            "segments": segments,
            "duration_seconds": duration,
            "credits_used": required,
            "credits_remaining": remaining,
        }
    except HTTPException:
        if reserved_credits:
            credit_store.refund(user.user_id, reserved_credits)
        raise
    except GroqTranscriberError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        if reserved_credits:
            credit_store.refund(user.user_id, reserved_credits)
        raise HTTPException(
            status_code=500,
            detail=f"Error inesperado del proxy de transcripcion: {exc}",
        ) from exc
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
