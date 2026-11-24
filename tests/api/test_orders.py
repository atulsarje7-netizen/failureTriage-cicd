from __future__ import annotations

import pytest

from tests.api.conftest import RecordingClient
from tests.api.helpers import login

pytestmark = pytest.mark.flow("checkout")


def test_checkout_empty_cart(api_client: RecordingClient) -> None:
    """Checkout with no items returns 400."""
    login(api_client)
    response = api_client.post("/checkout")
    assert response.status_code == 400


def test_checkout_creates_order_and_clears_cart(api_client: RecordingClient) -> None:
    """Checkout pays the cart, empties it, and lists the new order."""
    login(api_client)
    api_client.put("/cart/items", json={"product_id": 1, "quantity": 1})
    checkout = api_client.post("/checkout")
    assert checkout.status_code == 201
    order = checkout.json()
    assert order["status"] == "paid"
    assert order["total_cents"] == 1299
    assert order["items"][0]["sku"] == "WIDGET"

    cart = api_client.get("/cart")
    assert cart.json()["items"] == []

    listed = api_client.get("/orders")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == order["id"]

    fetched = api_client.get(f"/orders/{order['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == order["id"]


def test_order_not_found(api_client: RecordingClient) -> None:
    """Unknown order id returns 404."""
    login(api_client)
    response = api_client.get("/orders/999")
    assert response.status_code == 404
