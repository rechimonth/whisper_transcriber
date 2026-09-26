"""Configuracion de SQLAlchemy para PostgreSQL (local o Supabase).

La URL de conexion se lee siempre desde ``DATABASE_URL`` en ``backend/.env``.
Si no esta definida, se usa SQLite local como fallback de desarrollo para que
los tests y el arranque en frio no requieran Postgres.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Cargar backend/.env antes de leer DATABASE_URL.
# (Ojo: Path.with_name() reemplaza el ultimo componente, por eso se usa /.)
_backend_env = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_backend_env, override=False)
load_dotenv(Path(__file__).with_name(".env"), override=False)
load_dotenv(Path(__file__).parent.parent.with_name(".env"), override=False)

BACKEND_DIR = Path(__file__).resolve().parent.parent
_FALLBACK_SQLITE = f"sqlite:///{(BACKEND_DIR / 'dev.db').as_posix()}"


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip() or _FALLBACK_SQLITE


DATABASE_URL: str = get_database_url()

connect_args: dict = {}
if DATABASE_URL.startswith("sqlite"):
    # SQLite necesita check_same_thread=False para el uso con FastAPI.
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal: sessionmaker[Session] = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """Base declarativa compartida por todos los modelos."""


def get_db() -> Iterator[Session]:
    """Dependencia FastAPI: provee una sesion DB por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
