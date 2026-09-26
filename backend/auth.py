"""Autenticacion mock basada en Bearer tokens, preparada para sustituir el store por una base de datos."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str


def _load_tokens() -> Dict[str, AuthenticatedUser]:
    raw = os.getenv("AUTH_TOKENS_JSON", "").strip()
    if raw:
        try:
            payload = json.loads(raw)
            return {
                token: AuthenticatedUser(
                    user_id=str(value["user_id"]),
                    email=str(
                        value.get(
                            "email",
                            f"{value['user_id']}@example.local",
                        )
                    ),
                )
                for token, value in payload.items()
            }
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError(
                "AUTH_TOKENS_JSON no contiene un objeto valido."
            ) from exc

    token = os.getenv("MOCK_AUTH_TOKEN", "demo-token").strip()
    user_id = os.getenv("MOCK_USER_ID", "demo-user").strip()
    email = os.getenv("MOCK_USER_EMAIL", "demo@example.local").strip()
    return {
        token: AuthenticatedUser(
            user_id=user_id,
            email=email,
        )
    }


TOKENS = _load_tokens()


class LoginRequest(BaseModel):
    token: str = Field(min_length=8, max_length=512)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = TOKENS.get(credentials.credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso invalido.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def current_user_dependency(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthenticatedUser:
    """Alias estable para inyectar autenticacion en otros routers."""
    return get_current_user(credentials)


@router.post("/login")
def login(payload: LoginRequest) -> dict:
    user = TOKENS.get(payload.token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso invalido.",
        )
    return {
        "access_token": payload.token,
        "token_type": "bearer",
        "user_id": user.user_id,
        "email": user.email,
    }


@router.get("/me")
def me(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    return {
        "user_id": user.user_id,
        "email": user.email,
    }
