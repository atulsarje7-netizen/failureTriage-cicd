from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.main import create_app
from app.payment import PaymentError, charge
from app.payment_stub.main import app as payment_app


class StubServer:
    """Payment stub bound to a free local port."""

    def __init__(self) -> None:
        """Pick a port and prepare a daemon server."""
        self.port = _free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(payment_app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._server.install_signal_handlers = lambda: None
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        """Start the stub and wait until /health answers."""
        self._thread.start()
        deadline = time.time() + 5
        url = f"{self.url}/health"
        while time.time() < deadline:
            try:
                httpx.get(url, timeout=0.2)
            except httpx.HTTPError:
                time.sleep(0.05)
            else:
                return
        raise RuntimeError("payment stub did not start")

    def stop(self) -> None:
        """Ask the server thread to exit."""
        self._server.should_exit = True
        self._thread.join(timeout=2)

    @property
    def url(self) -> str:
        """Base URL of the stub."""
        return f"http://127.0.0.1:{self.port}"


def _free_port() -> int:
    """Return a local port that was free at bind time."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_charge_skips_http_without_payment_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no PAYMENT_URL the shop approves payment in process."""
    monkeypatch.delenv("PAYMENT_URL", raising=False)
    assert charge(100, "user-1") == "in-process"


def test_charge_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow stub becomes a 503 payment timeout."""
    server = StubServer()
    server.start()
    try:
        monkeypatch.setenv("PAYMENT_URL", server.url)
        monkeypatch.setenv("PAYMENT_STUB_DELAY_MS", "2000")
        monkeypatch.setenv("PAYMENT_TIMEOUT_S", "0.2")
        monkeypatch.setenv("PAYMENT_STUB_TOKEN", "")
        with pytest.raises(PaymentError) as raised:
            charge(100, "user-1")
        assert raised.value.status_code == 503
        assert raised.value.detail == "payment timeout"
    finally:
        server.stop()


def test_checkout_maps_payment_500_to_502(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A processor 500 becomes a 502 from checkout and does not create an order."""
    server = StubServer()
    server.start()
    try:
        monkeypatch.setenv("PAYMENT_URL", server.url)
        monkeypatch.setenv("PAYMENT_STUB_MODE", "error")
        monkeypatch.setenv("PAYMENT_STUB_TOKEN", "")
        monkeypatch.setenv("PAYMENT_API_KEY", "")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'pay.db'}")
        with TestClient(create_app()) as client:
            login = client.post(
                "/auth/login", json={"username": "alice", "password": "password"}
            )
            assert login.status_code == 200
            client.headers["Authorization"] = f"Bearer {login.json()['token']}"
            added = client.put("/cart/items", json={"product_id": 1, "quantity": 1})
            assert added.status_code == 200
            checkout = client.post("/checkout")
            assert checkout.status_code == 502
            orders = client.get("/orders")
            assert orders.json() == []
    finally:
        server.stop()


def test_health_reports_payment_down(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Health is degraded when the payment URL refuses connections."""
    monkeypatch.setenv("PAYMENT_URL", f"http://127.0.0.1:{_free_port()}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'health.db'}")
    with TestClient(create_app()) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["database"]["status"] == "ok"
    assert body["payment"]["status"] == "error"
    assert body["payment"]["mode"] == "remote"
