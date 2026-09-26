"""Webhook seguro de Mercado Pago con HMAC-SHA256 e idempotencia."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import Any

import mercadopago
from fastapi import APIRouter, HTTPException, Query, Request, status

from backend.credits import credit_store, get_package

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
_SIGNATURE_PARTS = re.compile(r"(?:^|,)\s*([a-zA-Z0-9_-]+)=([^,]*)")
_EXTERNAL_REFERENCE_PARTS = re.compile(
    r"^transcriber:(?P<user_id>[^:]+):(?P<package_id>[^:]+):(?P<nonce>[A-Za-z0-9]+)$"
)


def _parse_signature(header: str) -> dict[str, str]:
    return {
        key: value.strip()
        for key, value in _SIGNATURE_PARTS.findall(header or "")
    }


def validate_x_signature(
    x_signature: str,
    x_request_id: str,
    data_id: str,
) -> None:
    secret = os.getenv("MP_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="MP_WEBHOOK_SECRET no esta configurado.",
        )

    if not x_request_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta x-request-id.",
        )

    parts = _parse_signature(x_signature)
    ts = parts.get("ts")
    v1 = parts.get("v1")
    if not ts or not v1 or not data_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firma de Mercado Pago incompleta.",
        )

    try:
        timestamp = int(ts)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Timestamp de firma invalido.",
        ) from exc

    max_age = int(os.getenv("MP_WEBHOOK_MAX_AGE_SECONDS", "300"))
    if abs(time.time() - timestamp) > max_age:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firma de Mercado Pago expirada.",
        )

    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    expected = hmac.new(
        secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, v1):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firma de Mercado Pago invalida.",
        )


def _fetch_payment(payment_id: str) -> dict[str, Any]:
    token = os.getenv("MP_ACCESS_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Falta MP_ACCESS_TOKEN para consultar el pago.",
        )
    try:
        result = mercadopago.SDK(token).payment().get(payment_id)
        response = result.get("response", result) if isinstance(result, dict) else {}
        return response if isinstance(response, dict) else {}
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo consultar el pago en Mercado Pago: {exc}",
        ) from exc


@router.post("/mercadopago")
async def mercadopago_webhook(
    request: Request,
    data_id: str | None = Query(default=None, alias="data.id"),
) -> dict:
    x_signature = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON invalido.") from exc

    resolved_data_id = (
        str(data_id)
        if data_id
        else str(((body.get("data") or {}).get("id") or body.get("id") or ""))
    )
    validate_x_signature(
        x_signature=x_signature,
        x_request_id=x_request_id,
        data_id=resolved_data_id,
    )

    payment_id = resolved_data_id
    payment = _fetch_payment(payment_id)
    status_name = str(payment.get("status", "")).lower()
    if status_name != "approved":
        return {
            "received": True,
            "processed": False,
            "status": status_name or "unknown",
        }

    external_reference = str(payment.get("external_reference", ""))
    match = _EXTERNAL_REFERENCE_PARTS.match(external_reference)
    if not match:
        return {
            "received": True,
            "processed": False,
            "reason": "external_reference no pertenece al sistema",
        }

    user_id = match.group("user_id")
    package_id = match.group("package_id")
    try:
        package = get_package(package_id)
    except KeyError:
        return {
            "received": True,
            "processed": False,
            "reason": "package_id desconocido",
        }

    expected_currency = os.getenv("MP_CURRENCY_ID", "ARS").upper()
    currency = str(payment.get("currency_id", "")).upper()
    raw_amount = payment.get("transaction_amount")
    try:
        transaction_amount = float(raw_amount)
    except (TypeError, ValueError):
        transaction_amount = -1.0

    if (
        transaction_amount < 0
        or abs(transaction_amount - package.price_ars) > 0.01
        or (currency and currency != expected_currency)
    ):
        return {
            "received": True,
            "processed": False,
            "reason": "importe o moneda del pago no coincide con el paquete",
        }

    applied, balance = credit_store.apply_payment_once(
        payment_id=payment_id,
        user_id=user_id,
        credits=package.credits,
    )

    return {
        "received": True,
        "processed": applied,
        "payment_id": payment_id,
        "user_id": user_id,
        "credits_added": package.credits if applied else 0,
        "credits_balance": balance,
    }
