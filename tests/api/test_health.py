from __future__ import annotations

import pytest

from tests.api.conftest import RecordingClient

pytestmark = pytest.mark.flow("health")


def test_health_reports_database_and_payment(api_client: RecordingClient) -> None:
    """Health is ok and echoes X-Test-Id and X-Request-Id."""
    response = api_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "ok"
    assert body["payment"]["status"] == "ok"
    assert response.headers["x-test-id"] == api_client.test_id
    assert "x-request-id" in response.headers
