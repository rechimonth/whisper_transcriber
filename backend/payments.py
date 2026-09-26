"""Integracion con Mercado Pago Checkout Pro."""
from __future__ import annotations

import os
import uuid

import mercadopago
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth import AuthenticatedUser, current_user_dependency
from backend.credits import get_package

router = APIRouter(prefix="/payments", tags=["payments"])


class PreferenceRequest(BaseModel):
    package_id: str = Field(default="starter", min_length=1, max_length=50)


def _sdk() -> mercadopago.SDK:
    token = os.getenv("MP_ACCESS_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Mercado Pago no esta configurado: falta MP_ACCESS_TOKEN.",
        )
    return mercadopago.SDK(token)


@router.post("/preference")
def create_preference(
    payload: PreferenceRequest,
    user: AuthenticatedUser = Depends(current_user_dependency),
) -> dict:
    try:
        package = get_package(payload.package_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    external_reference = (
        f"transcriber:{user.user_id}:{package.package_id}:{uuid.uuid4().hex}"
    )

    preference_data = {
        "items": [
            {
                "id": package.package_id,
                "title": package.title,
                "quantity": 1,
                "currency_id": os.getenv("MP_CURRENCY_ID", "ARS"),
                "unit_price": package.price_ars,
            }
        ],
        "external_reference": external_reference,
    }

    webhook_url = os.getenv("MP_WEBHOOK_URL", "").strip()
    if webhook_url:
        preference_data["notification_url"] = webhook_url

    success_url = os.getenv("MP_SUCCESS_URL", "").strip()
    failure_url = os.getenv("MP_FAILURE_URL", "").strip()
    pending_url = os.getenv("MP_PENDING_URL", "").strip()
    if success_url or failure_url or pending_url:
        preference_data["back_urls"] = {
            key: value
            for key, value in {
                "success": success_url,
                "failure": failure_url,
                "pending": pending_url,
            }.items()
            if value
        }
        if success_url:
            preference_data["auto_return"] = "approved"

    try:
        result = _sdk().preference().create(preference_data)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo crear la preferencia de Mercado Pago: {exc}",
        ) from exc

    response = result.get("response", result) if isinstance(result, dict) else {}
    init_point = response.get("init_point") or response.get("sandbox_init_point")
    if not init_point:
        raise HTTPException(
            status_code=502,
            detail="Mercado Pago no devolvio una URL de Checkout Pro.",
        )

    return {
        "preference_id": response.get("id"),
        "init_point": init_point,
        "package_id": package.package_id,
        "credits": package.credits,
        "price_ars": package.price_ars,
        "external_reference": external_reference,
    }
