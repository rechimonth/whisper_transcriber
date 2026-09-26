"""Prueba E2E del SaaS simulando el cliente de escritorio.

Flujo (igual que ui/main_window.py + core/backend_client.py contra el backend):
  1. POST /auth/register con email/password aleatorios.
  2. POST /auth/login (form OAuth2) para obtener el JWT.
  3. GET /credits con `Authorization: Bearer <token>`.
  4. assert de que el saldo es exactamente 60 (bono de bienvenida atomico).

Uso:
  python test_e2e_api.py   # requiere el backend en http://localhost:8000
"""
from __future__ import annotations

import sys
import uuid

import requests

BASE_URL = "http://localhost:8000"
WELCOME_BONUS = 60


def main() -> None:
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    password = f"Clave-{uuid.uuid4().hex[:12]}!"

    response = requests.post(
        f"{BASE_URL}/auth/register",
        json={"email": email, "password": password},
        timeout=30,
    )
    assert response.status_code == 201, f"Registro fallo: {response.text}"
    print(f"[OK] Registro exitoso: {email} (user_id={response.json()['user_id']})")

    response = requests.post(
        f"{BASE_URL}/auth/login",
        data={"username": email, "password": password},
        timeout=30,
    )
    assert response.status_code == 200, f"Login fallo: {response.text}"
    token = response.json()["access_token"]
    print(f"[OK] Login exitoso, JWT recibido ({len(token)} caracteres)")

    response = requests.get(
        f"{BASE_URL}/credits",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert response.status_code == 200, f"Saldo fallo: {response.text}"
    credits = response.json()["credits"]
    print(f"[OK] Saldo actual: {credits} creditos")

    assert credits == WELCOME_BONUS, (
        f"Bono de bienvenida incorrecto: esperado {WELCOME_BONUS}, "
        f"obtenido {credits}"
    )
    print("[OK] Bono de bienvenida validado: 60 creditos exactos. E2E verde.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"[FAIL] No se pudo conectar con el backend en {BASE_URL}: {exc}")
        sys.exit(2)
