"""Saldo de creditos y operaciones atomicas.

El almacenamiento principal es PostgreSQL (modelos ``CreditWallet`` y
``Transaction``). ``CreditStore`` in-memory se conserva solo por
compatibilidad con tests historicos y esta deprecado: el codigo de
produccion debe usar las funciones ``*_db`` de este modulo.
"""
from __future__ import annotations

import json
import math
import os
import threading
import warnings
from dataclasses import dataclass
from typing import Dict, Set

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.models import CreditWallet, Transaction


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(int(raw), minimum)
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float = 0.01) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(float(raw), minimum)
    except ValueError:
        return default


CREDITS_PER_MINUTE = _env_float("CREDITS_PER_MINUTE", 1.0)
DEFAULT_INITIAL_CREDITS = _env_int("DEFAULT_INITIAL_CREDITS", 60)
PAYMENT_PACKAGES_ENV = os.getenv("CREDIT_PACKAGES_JSON", "").strip()


@dataclass(frozen=True)
class CreditPackage:
    package_id: str
    credits: int
    price_ars: float
    title: str


def _load_packages() -> Dict[str, CreditPackage]:
    if PAYMENT_PACKAGES_ENV:
        try:
            payload = json.loads(PAYMENT_PACKAGES_ENV)
            return {
                package_id: CreditPackage(
                    package_id=package_id,
                    credits=int(data["credits"]),
                    price_ars=float(data["price_ars"]),
                    title=str(data.get("title", f"{data['credits']} creditos")),
                )
                for package_id, data in payload.items()
            }
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError(
                "CREDIT_PACKAGES_JSON no contiene paquetes validos."
            ) from exc

    return {
        "starter": CreditPackage(
            package_id="starter",
            credits=60,
            price_ars=2990.0,
            title="60 creditos de TranscriptorVideoIA",
        ),
        "pro": CreditPackage(
            package_id="pro",
            credits=180,
            price_ars=6990.0,
            title="180 creditos de TranscriptorVideoIA",
        ),
    }


CREDIT_PACKAGES = _load_packages()


class InsufficientCreditsError(Exception):
    """El usuario no tiene saldo suficiente."""


class CreditStore:
    """DEPRECADO: store in-memory del MVP. Usar las funciones ``*_db``."""

    def __init__(self) -> None:
        warnings.warn(
            "CreditStore esta deprecado; usa get_balance_db/apply_payment_once_db.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._lock = threading.RLock()
        self._balances: Dict[str, int] = {}
        self._processed_payments: Set[str] = set()

    def get_balance(self, user_id: str) -> int:
        with self._lock:
            return self._balances.setdefault(user_id, DEFAULT_INITIAL_CREDITS)

    def reserve(self, user_id: str, credits: int) -> int:
        if credits <= 0:
            raise ValueError("La cantidad de creditos debe ser positiva.")
        with self._lock:
            current = self._balances.setdefault(user_id, DEFAULT_INITIAL_CREDITS)
            if current < credits:
                raise InsufficientCreditsError(
                    f"Saldo insuficiente: requiere {credits} y dispone de {current}."
                )
            self._balances[user_id] = current - credits
            return self._balances[user_id]

    def refund(self, user_id: str, credits: int) -> int:
        if credits < 0:
            raise ValueError("No se puede reintegrar una cantidad negativa.")
        with self._lock:
            current = self._balances.setdefault(user_id, DEFAULT_INITIAL_CREDITS)
            self._balances[user_id] = current + credits
            return self._balances[user_id]

    def add(self, user_id: str, credits: int) -> int:
        if credits <= 0:
            raise ValueError("La cantidad de creditos debe ser positiva.")
        with self._lock:
            current = self._balances.setdefault(user_id, DEFAULT_INITIAL_CREDITS)
            self._balances[user_id] = current + credits
            return self._balances[user_id]

    def apply_payment_once(
        self, payment_id: str, user_id: str, credits: int
    ) -> tuple[bool, int]:
        """Acredita un pago una sola vez incluso ante reenvios concurrentes."""
        with self._lock:
            if payment_id in self._processed_payments:
                return False, self._balances.setdefault(
                    user_id, DEFAULT_INITIAL_CREDITS
                )
            self._processed_payments.add(payment_id)
            current = self._balances.setdefault(user_id, DEFAULT_INITIAL_CREDITS)
            self._balances[user_id] = current + credits
            return True, self._balances[user_id]


credit_store = CreditStore()


def credits_for_duration(duration_seconds: float) -> int:
    if duration_seconds <= 0:
        raise ValueError("La duracion debe ser mayor que cero.")
    minutes = duration_seconds / 60.0
    return max(1, math.ceil(minutes * CREDITS_PER_MINUTE))


def get_package(package_id: str) -> CreditPackage:
    package = CREDIT_PACKAGES.get(package_id)
    if package is None:
        raise KeyError(f"Paquete desconocido: {package_id}")
    return package


# ---------------------------------------------------------------------------
# Capa de persistencia PostgreSQL (produccion)
# ---------------------------------------------------------------------------

def _get_wallet_for_update(db: Session, user_id: int) -> CreditWallet | None:
    """Devuelve la wallet bloqueada (SELECT ... FOR UPDATE en Postgres)."""
    return (
        db.query(CreditWallet)
        .filter(CreditWallet.user_id == user_id)
        .with_for_update()
        .first()
    )


def get_balance_db(db: Session, user_id: int) -> int:
    """Saldo actual; crea la wallet con el bono inicial si no existe."""
    wallet = db.query(CreditWallet).filter(CreditWallet.user_id == user_id).first()
    if wallet is None:
        wallet = CreditWallet(user_id=user_id, balance=DEFAULT_INITIAL_CREDITS)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet.balance


def apply_payment_once_db(
    db: Session, *, payment_id: str, user_id: int, credits: int
) -> tuple[bool, int]:
    """Acredita un pago de Mercado Pago de forma atomica e idempotente.

    Inserta el ``Transaction`` (``payment_id`` UNIQUE) y actualiza la wallet
    en la misma transaccion. Si el ``payment_id`` ya existe, hace rollback y
    devuelve ``(False, saldo_actual)`` sin modificar nada.
    """
    if credits <= 0:
        raise ValueError("La cantidad de creditos debe ser positiva.")
    try:
        db.add(
            Transaction(
                user_id=user_id,
                amount=credits,
                payment_id=payment_id,
                transaction_type="purchase",
            )
        )
        db.flush()  # fuerza el UNIQUE de payment_id dentro de la transaccion
        wallet = _get_wallet_for_update(db, user_id)
        if wallet is None:
            wallet = CreditWallet(user_id=user_id, balance=0)
            db.add(wallet)
            db.flush()
        wallet.balance += credits
        db.commit()
        return True, wallet.balance
    except IntegrityError:
        db.rollback()
        wallet = db.query(CreditWallet).filter(CreditWallet.user_id == user_id).first()
        current = wallet.balance if wallet is not None else 0
        return False, current


def debit_for_usage_db(
    db: Session, *, user_id: int, credits: int
) -> int:
    """Descuenta creditos por una transcripcion de forma atomica.

    Bloquea la wallet, verifica saldo, descuenta y registra el
    ``Transaction`` de tipo 'usage' en un unico commit. Lanza
    ``InsufficientCreditsError`` si el saldo no alcanza (con rollback).
    """
    if credits <= 0:
        raise ValueError("La cantidad de creditos debe ser positiva.")
    try:
        wallet = _get_wallet_for_update(db, user_id)
        if wallet is None:
            raise InsufficientCreditsError(
                f"Saldo insuficiente: requiere {credits} y dispone de 0."
            )
        if wallet.balance < credits:
            raise InsufficientCreditsError(
                f"Saldo insuficiente: requiere {credits} "
                f"y dispone de {wallet.balance}."
            )
        wallet.balance -= credits
        db.add(
            Transaction(
                user_id=user_id,
                amount=-credits,
                payment_id=None,
                transaction_type="usage",
            )
        )
        db.commit()
        return wallet.balance
    except InsufficientCreditsError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
