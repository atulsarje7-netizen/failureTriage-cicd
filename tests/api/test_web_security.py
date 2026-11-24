from __future__ import annotations

import pytest

from tests.api.conftest import RecordingClient

pytestmark = pytest.mark.flow("cart")


def _csrf(client: RecordingClient) -> str:
    """Read the CSRF cookie after a GET, fetching /login if needed."""
    token = client._client.cookies.get("csrf")
    if token:
        return str(token)
    page = client.get("/login")
    assert page.status_code == 200
    return str(page.cookies["csrf"])


def _html_login(client: RecordingClient) -> None:
    """Log in through the HTML form so a session cookie is set."""
    csrf = _csrf(client)
    response = client.post(
        "/login",
        data={"username": "alice", "password": "password", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert response.status_code == 200


def test_html_add_to_cart_requires_csrf(api_client: RecordingClient) -> None:
    """HTML cart POST without a CSRF token is rejected."""
    _html_login(api_client)
    response = api_client.post("/basket/items", data={"product_id": "1", "quantity": "1"})
    assert response.status_code == 403


def test_html_rejects_negative_quantity(api_client: RecordingClient) -> None:
    """HTML cart POST does not accept a negative quantity."""
    _html_login(api_client)
    csrf = _csrf(api_client)
    response = api_client.post(
        "/basket/items",
        data={"product_id": "1", "quantity": "-2", "csrf_token": csrf},
    )
    assert response.status_code in {400, 422}


def test_session_cookie_does_not_unlock_json_api(api_client: RecordingClient) -> None:
    """JSON cart routes require Bearer auth, not the HTML session cookie."""
    _html_login(api_client)
    response = api_client.get("/cart")
    assert response.status_code == 401
