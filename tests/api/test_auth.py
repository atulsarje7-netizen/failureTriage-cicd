from __future__ import annotations

import pytest

from tests.api.conftest import RecordingClient
from tests.api.helpers import login

pytestmark = pytest.mark.flow("login")


def test_login_success(api_client: RecordingClient) -> None:
    response = api_client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["token"]


def test_login_rejects_bad_password(api_client: RecordingClient) -> None:
    response = api_client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert response.status_code == 401


def test_me_requires_auth(api_client: RecordingClient) -> None:
    response = api_client.get("/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user(api_client: RecordingClient) -> None:
    login(api_client)
    response = api_client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "alice"
