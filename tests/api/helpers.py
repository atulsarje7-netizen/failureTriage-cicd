from __future__ import annotations

from tests.api.conftest import RecordingClient


def login(client: RecordingClient, username: str = "alice", password: str = "password") -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    client.authenticate(token)
    return token
