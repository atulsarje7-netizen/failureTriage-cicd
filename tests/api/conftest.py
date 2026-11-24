from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.seed import reset_database
from tests.api.exchange import HttpExchange, exchange_dir, safe_nodeid, write_exchange


class RecordingClient:
    """httpx-style client that stores the last request/response for failure capture."""

    def __init__(self, client: TestClient, test_id: str) -> None:
        """Wrap a TestClient and tag calls with this test's node id."""
        self._client = client
        self.test_id = test_id
        self.last: HttpExchange | None = None

    def authenticate(self, token: str) -> None:
        """Send later requests with this Bearer token."""
        self._client.headers["Authorization"] = f"Bearer {token}"

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send an HTTP request and remember the exchange."""
        body = kwargs.get("json", kwargs.get("content"))
        headers = {**dict(self._client.headers), **kwargs.get("headers", {})}
        started = time.perf_counter()
        response = self._client.request(method, url, **kwargs)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = response.text
        self.last = HttpExchange(
            method=method.upper(),
            url=str(response.request.url),
            request_headers={k: str(v) for k, v in headers.items()},
            request_body=body,
            status_code=response.status_code,
            response_headers={k: v for k, v in response.headers.items()},
            response_body=response_body,
            latency_ms=latency_ms,
            test_id=self.test_id,
        )
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET wrapper around request()."""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """POST wrapper around request()."""
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        """PUT wrapper around request()."""
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        """DELETE wrapper around request()."""
        return self.request("DELETE", url, **kwargs)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark shop tests so a fault run can ignore catalog unit tests."""
    for item in items:
        if "api_client" in item.fixturenames:
            item.add_marker(pytest.mark.app)


@pytest.fixture
def api_client(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[RecordingClient]:
    """App client on a temp SQLite file, or a reseeded shared DB when USE_SHARED_DB=1."""
    test_id = request.node.nodeid
    log_path = Path(os.environ.get("APP_LOG_PATH", str(tmp_path / "app.log")))
    shared = os.environ.get("USE_SHARED_DB") == "1"
    if not shared:
        db_path = tmp_path / "shop.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("APP_LOG_PATH", str(log_path))
    app = create_app()
    with TestClient(app, headers={"X-Test-Id": test_id}) as raw:
        if shared:
            reset_database(app.state.SessionLocal)
        yield RecordingClient(raw, test_id)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    """On a failed test, write the last HTTP exchange to disk."""
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    client = item.funcargs.get("api_client")
    if not isinstance(client, RecordingClient) or client.last is None:
        return
    path = exchange_dir() / f"{safe_nodeid(item.nodeid)}.json"
    write_exchange(path, client.last)
