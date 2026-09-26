"""Cliente HTTP del escritorio para TranscriptorVideoIA."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class BackendClientError(Exception):
    message: str
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


class AuthenticationError(BackendClientError):
    """Token invalido o sesion no autorizada."""


class InsufficientCreditsError(BackendClientError):
    """Saldo insuficiente para la transcripcion."""


class BackendUnavailableError(BackendClientError):
    """El backend no esta disponible."""


class BackendClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: tuple[float, float] = (15.0, 7200.0),
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("TRANSCRIBER_BACKEND_URL", "http://localhost:8000")
        ).strip().rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.token: str | None = None
        self.user: dict[str, Any] | None = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise AuthenticationError("Inicia sesion antes de usar el backend.")
        return {"Authorization": f"Bearer {self.token}"}

    @staticmethod
    def _detail(response: requests.Response) -> str:
        try:
            payload = response.json()
            detail = payload.get("detail")
            if detail:
                return str(detail)
        except ValueError:
            pass
        return response.text.strip() or f"Error HTTP {response.status_code}"

    def login(self, access_token: str) -> dict[str, Any]:
        token = access_token.strip()
        if len(token) < 8:
            raise AuthenticationError("El token debe tener al menos 8 caracteres.")

        try:
            response = self.session.post(
                f"{self.base_url}/auth/login",
                json={"token": token},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise BackendUnavailableError(
                f"No se pudo conectar con el backend: {exc}"
            ) from exc

        if response.status_code == 401:
            raise AuthenticationError(self._detail(response), response.status_code)
        if response.status_code >= 400:
            raise BackendClientError(self._detail(response), response.status_code)

        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendClientError(
                "El backend devolvio una respuesta no valida."
            ) from exc

        self.token = str(payload["access_token"])
        self.user = payload
        return payload

    def get_credits(self) -> int:
        try:
            response = self.session.get(
                f"{self.base_url}/credits",
                headers=self._headers(),
                timeout=15,
            )
        except requests.RequestException as exc:
            raise BackendUnavailableError(
                f"No se pudo consultar el saldo: {exc}"
            ) from exc

        if response.status_code == 401:
            raise AuthenticationError(self._detail(response), response.status_code)
        if response.status_code >= 400:
            raise BackendClientError(self._detail(response), response.status_code)

        try:
            credits = int(response.json()["credits"])
        except (ValueError, KeyError, TypeError) as exc:
            raise BackendClientError("Respuesta de saldo invalida.") from exc

        return max(credits, 0)

    def create_checkout(self, package_id: str = "starter") -> str:
        try:
            response = self.session.post(
                f"{self.base_url}/payments/preference",
                json={"package_id": package_id},
                headers=self._headers(),
                timeout=30,
            )
        except requests.RequestException as exc:
            raise BackendUnavailableError(
                f"No se pudo conectar con Mercado Pago a traves del backend: {exc}"
            ) from exc

        if response.status_code == 401:
            raise AuthenticationError(self._detail(response), response.status_code)
        if response.status_code >= 400:
            raise BackendClientError(self._detail(response), response.status_code)

        try:
            init_point = str(response.json()["init_point"])
        except (ValueError, KeyError, TypeError) as exc:
            raise BackendClientError(
                "Mercado Pago no devolvio una URL de checkout valida."
            ) from exc

        if not init_point.startswith(("https://", "http://")):
            raise BackendClientError("La URL de checkout del backend no es valida.")

        return init_point

    def transcribe(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"El archivo no existe: {file_path}")

        try:
            with path.open("rb") as audio_file:
                response = self.session.post(
                    f"{self.base_url}/transcribe",
                    headers=self._headers(),
                    files={
                        "audio": (
                            path.name,
                            audio_file,
                            "application/octet-stream",
                        )
                    },
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise BackendUnavailableError(
                f"No se pudo conectar con el backend: {exc}"
            ) from exc

        if response.status_code == 401:
            raise AuthenticationError(self._detail(response), response.status_code)
        if response.status_code == 402:
            raise InsufficientCreditsError(
                self._detail(response), response.status_code
            )
        if response.status_code >= 400:
            raise BackendClientError(self._detail(response), response.status_code)

        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendClientError(
                "El backend devolvio una transcripcion no valida."
            ) from exc

        if not isinstance(payload.get("segments"), list):
            raise BackendClientError("La respuesta no contiene segmentos validos.")

        return payload
