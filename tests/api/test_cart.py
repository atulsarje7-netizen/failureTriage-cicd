from __future__ import annotations

import pytest

from tests.api.conftest import RecordingClient
from tests.api.helpers import login

pytestmark = pytest.mark.flow("cart")


def test_cart_requires_auth(api_client: RecordingClient) -> None:
    response = api_client.get("/cart")
    assert response.status_code == 401


def test_add_and_remove_cart_item(api_client: RecordingClient) -> None:
    login(api_client)
    added = api_client.put("/cart/items", json={"product_id": 1, "quantity": 2})
    assert added.status_code == 200
    body = added.json()
    assert body["total_cents"] == 2598
    assert body["items"][0]["quantity"] == 2

    removed = api_client.delete("/cart/items/1")
    assert removed.status_code == 200
    assert removed.json()["items"] == []
    assert removed.json()["total_cents"] == 0
