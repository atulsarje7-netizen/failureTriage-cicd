from __future__ import annotations

import pytest

from tests.api.conftest import RecordingClient

pytestmark = pytest.mark.flow("catalog")


def test_list_products(api_client: RecordingClient) -> None:
    """Catalog lists the three seeded products."""
    response = api_client.get("/products")
    assert response.status_code == 200
    products = response.json()
    assert len(products) == 3
    assert {item["sku"] for item in products} == {"WIDGET", "GADGET", "DOODAD"}


def test_get_product(api_client: RecordingClient) -> None:
    """GET /products/1 returns the widget."""
    response = api_client.get("/products/1")
    assert response.status_code == 200
    assert response.json()["sku"] == "WIDGET"


def test_get_missing_product(api_client: RecordingClient) -> None:
    """Unknown product id returns 404."""
    response = api_client.get("/products/999")
    assert response.status_code == 404
