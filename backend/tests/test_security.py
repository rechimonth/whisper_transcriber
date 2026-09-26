"""Tests de seguridad: rate limiting de login y revocacion JWT (logout).

Ejecutar: pytest backend/tests/test_security.py
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.rate_limit import limiter


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture()
def client():
    return TestClient(app)


def _register(client: TestClient) -> tuple[str, str]:
    email = f"sec-{uuid.uuid4().hex[:8]}@example.com"
    password = f"Clave-{uuid.uuid4().hex[:12]}!"
    response = client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    return email, password


def test_login_rate_limited_after_five_attempts(client: TestClient):
    email, password = _register(client)
    form = {"username": email, "password": password}

    for _ in range(5):
        response = client.post("/auth/login", data=form)
        assert response.status_code == 200, response.text

    response = client.post("/auth/login", data=form)
    assert response.status_code == 429, response.text
    assert "detail" in response.json()


def test_logout_revokes_token_for_credits(client: TestClient):
    email, password = _register(client)
    login = client.post(
        "/auth/login", data={"username": email, "password": password}
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/credits", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["credits"] == 60

    logout = client.post("/auth/logout", headers=headers)
    assert logout.status_code == 200, logout.text
    assert logout.json()["logged_out"] is True

    revoked = client.get("/credits", headers=headers)
    assert revoked.status_code == 401, revoked.text

    again = client.post("/auth/logout", headers=headers)
    assert again.status_code == 401, again.text
