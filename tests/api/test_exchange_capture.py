from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.api.conftest import RecordingClient
from tests.api.exchange import HttpExchange, write_exchange


def test_write_exchange_round_trip(tmp_path: Path) -> None:
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


@pytest.mark.skipif(os.environ.get("DEMO_FAIL") != "1", reason="opt-in artifact demo")
def test_demo_deliberate_failure(api_client: RecordingClient) -> None:
    response = api_client.get("/health")
    assert response.status_code == 599
