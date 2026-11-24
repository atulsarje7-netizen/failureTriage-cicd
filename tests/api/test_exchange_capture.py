from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.api.conftest import RecordingClient
from tests.api.exchange import HttpExchange, write_exchange


def test_write_exchange_round_trip(tmp_path: Path) -> None:
    """write_exchange persists method, status, and test_id."""
    path = tmp_path / "exchange.json"
    exchange = HttpExchange(
        method="GET",
        url="http://testserver/health",
        request_headers={"x-test-id": "demo"},
        request_body=None,
        status_code=200,
        response_headers={"content-type": "application/json"},
        response_body={"status": "ok"},
        latency_ms=1.2,
        test_id="demo",
    )
    write_exchange(path, exchange)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["method"] == "GET"
    assert loaded["status_code"] == 200
    assert loaded["test_id"] == "demo"


def test_write_exchange_redacts_secrets(tmp_path: Path) -> None:
    """Passwords, tokens, and Authorization headers are not written to disk."""
    path = tmp_path / "exchange.json"
    exchange = HttpExchange(
        method="POST",
        url="http://testserver/auth/login",
        request_headers={"authorization": "Bearer secret", "x-test-id": "demo"},
        request_body={"username": "alice", "password": "password"},
        status_code=200,
        response_headers={"set-cookie": "session=abc"},
        response_body={"token": "secret-token", "username": "alice"},
        latency_ms=1.0,
        test_id="demo",
    )
    write_exchange(path, exchange)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["request_headers"]["authorization"] == "[redacted]"
    assert loaded["request_body"]["password"] == "[redacted]"
    assert loaded["response_body"]["token"] == "[redacted]"
    assert loaded["response_headers"]["set-cookie"] == "[redacted]"
    assert loaded["response_body"]["username"] == "alice"


@pytest.mark.skipif(os.environ.get("DEMO_FAIL") != "1", reason="opt-in artifact demo")
def test_demo_deliberate_failure(api_client: RecordingClient) -> None:
    """Opt-in failing test used to generate JUnit, log, and exchange files."""
    response = api_client.get("/health")
    assert response.status_code == 599
