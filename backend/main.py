"""Aplicacion FastAPI de TranscriptorVideoIA."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar la configuracion del backend antes de importar routers, ya que algunos
# modulos inicializan stores y clientes leyendo variables de entorno.
load_dotenv(Path(__file__).with_name(".env"), override=False)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from backend.auth import router as auth_router
from backend.payments import router as payments_router
from backend.proxy_groq import router as proxy_router
from backend.rate_limit import limiter
from backend.webhooks import router as webhooks_router

app = FastAPI(
    title="TranscriptorVideoIA Backend",
    version="1.0.0",
    description="Proxy seguro Groq + creditos + Mercado Pago.",
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Demasiadas peticiones. Espera un momento e intentalo de nuevo."
        },
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost,http://127.0.0.1",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(payments_router)
app.include_router(webhooks_router)
app.include_router(proxy_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "transcriptorvideoia-backend"}
