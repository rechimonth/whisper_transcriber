"""Autenticacion real con email/password (argon2) y tokens JWT.

- ``POST /auth/register``: crea el usuario y su wallet con el bono de
  bienvenida en una unica transaccion.
- ``POST /auth/login``: formulario OAuth2 (username=email, password),
  devuelve un JWT valido por 30 dias (app de escritorio).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm
from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.database import get_db
from backend.database.models import CreditWallet, TokenBlocklist, User
from backend.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

logger = logging.getLogger(__name__)

_DEFAULT_JWT_SECRET = "dev-secret-key-cambiar-en-produccion"
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", _DEFAULT_JWT_SECRET).strip()
if JWT_SECRET_KEY == _DEFAULT_JWT_SECRET:
    logger.warning(
        "JWT_SECRET_KEY no configurado: usando secreto de desarrollo. "
        "Cualquiera con el codigo fuente puede falsificar tokens. "
        "Define JWT_SECRET_KEY antes de exponer el backend."
    )
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256").strip()
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "43200"))

WELCOME_BONUS_CREDITS = int(os.getenv("DEFAULT_INITIAL_CREDITS", "60"))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: int
    email: str


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str


def _validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El email no tiene un formato valido.",
        )
    return normalized


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": str(user_id), "email": email, "jti": uuid4().hex, "exp": expire},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token ha expirado. Inicia sesion de nuevo.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso invalido.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _purge_expired_blocklist(db: Session) -> None:
    db.query(TokenBlocklist).filter(
        TokenBlocklist.expires_at < datetime.now(timezone.utc)
    ).delete()
    db.commit()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = _decode_token(credentials.credentials)
        user_id = int(payload.get("sub", ""))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso invalido.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    jti = payload.get("jti")
    if jti and db.query(TokenBlocklist).filter(TokenBlocklist.jti == jti).first():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesion fue cerrada. Inicia sesion de nuevo.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El usuario del token ya no existe.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthenticatedUser(user_id=user.id, email=user.email)


def current_user_dependency(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    """Alias estable para inyectar autenticacion en otros routers."""
    return get_current_user(credentials, db)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
def register(
    request: Request,
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    email = _validate_email(payload.email)

    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una cuenta con ese email.",
        )

    user = User(email=email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.flush()  # obtiene user.id sin cerrar la transaccion
    db.add(CreditWallet(user_id=user.id, balance=WELCOME_BONUS_CREDITS))
    db.commit()
    db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        user_id=user.id,
        email=user.email,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    email = form.username.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contrasena incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        user_id=user.id,
        email=user.email,
    )


@router.get("/me")
def me(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    return {
        "user_id": user.user_id,
        "email": user.email,
    }


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = _decode_token(credentials.credentials)
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este token no es revocable. Expirara por si solo.",
        )

    _purge_expired_blocklist(db)

    if db.query(TokenBlocklist).filter(TokenBlocklist.jti == jti).first():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesion ya fue cerrada.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        expires_at = datetime.fromtimestamp(float(payload["exp"]), tz=timezone.utc)
    except (KeyError, ValueError, TypeError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El token no tiene expiracion valida.",
        ) from exc

    db.add(TokenBlocklist(jti=jti, expires_at=expires_at))
    db.commit()
    return {"logged_out": True}
