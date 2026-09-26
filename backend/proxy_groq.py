"""Proxy del backend hacia Groq.

El cliente de escritorio nunca recibe la API key. El servidor verifica el
saldo en PostgreSQL, transcribe y descuenta los creditos con un debito
atomico (wallet + registro 'usage' en un unico commit).
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.auth import AuthenticatedUser, current_user_dependency
from backend.credits import (
    InsufficientCreditsError,
    credits_for_duration,
    debit_for_usage_db,
    get_balance_db,
)
from backend.database.database import get_db
from core.audio_utils import get_audio_duration
from core.groq_transcriber import (
    GroqTranscriber,
    GroqTranscriberError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["transcription"])

SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".m4a"
}


def _env_max_upload_bytes() -> int:
    raw = os.getenv("BACKEND_MAX_UPLOAD_BYTES", "")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            logger.warning(
                "BACKEND_MAX_UPLOAD_BYTES invalido (%r); usando 2 GiB.", raw
            )
    return 2 * 1024 * 1024 * 1024


MAX_UPLOAD_BYTES = _env_max_upload_bytes()
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
def get_credits(
    user: AuthenticatedUser = Depends(current_user_dependency),
    db: Session = Depends(get_db),
) -> dict:
    return {
        "user_id": user.user_id,
        "credits": get_balance_db(db, user.user_id),
    }


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    user: AuthenticatedUser = Depends(current_user_dependency),
    db: Session = Depends(get_db),
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
        balance = get_balance_db(db, user.user_id)
        if balance < required:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Saldo insuficiente: requiere {required} y dispone de "
                    f"{balance}."
                ),
            )

        transcriber = GroqTranscriber(
            api_key=server_key,
            max_workers=min(int(os.getenv("GROQ_MAX_WORKERS", "3")), 3),
        )
        segments = transcriber.transcribe_file(
            str(path),
            language=None,
            checkpoint_id=f"{user.user_id}-{file_hash[:20]}",
        )

        try:
            remaining = debit_for_usage_db(
                db, user_id=user.user_id, credits=required
            )
        except InsufficientCreditsError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

        return {
            "text": _segments_to_text(segments),
            "segments": segments,
            "duration_seconds": duration,
            "credits_used": required,
            "credits_remaining": remaining,
        }
    except HTTPException:
        raise
    except GroqTranscriberError as exc:
        logger.warning("Fallo de Groq para user_id=%s: %s", user.user_id, exc)
        raise HTTPException(
            status_code=502,
            detail="El servicio de transcripcion no esta disponible. "
            "Intentalo de nuevo mas tarde.",
        ) from exc
    except Exception as exc:
        logger.exception("Error inesperado del proxy de transcripcion")
        raise HTTPException(
            status_code=500,
            detail="Error interno del servidor de transcripcion.",
        ) from exc
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
