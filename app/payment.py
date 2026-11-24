from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("app.payment")


class PaymentError(Exception):
    """Payment stub call failed with an HTTP status the shop should return."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Store the status and a short detail safe to show clients."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def payment_base_url() -> str:
    """Return PAYMENT_URL without a trailing slash, or empty when unset."""
    return os.environ.get("PAYMENT_URL", "").strip().rstrip("/")


def charge(amount_cents: int, reference: str) -> str:
    """Collect payment and return the provider reference, or an in-process marker."""
    base = payment_base_url()
    if not base:
        return "in-process"
    timeout = float(os.environ.get("PAYMENT_TIMEOUT_S", "5"))
    headers: dict[str, str] = {}
    api_key = os.environ.get("PAYMENT_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = httpx.post(
            f"{base}/charge",
            json={"amount_cents": amount_cents, "reference": reference},
            headers=headers,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        logger.warning("payment_timeout reference=%s", reference)
        raise PaymentError(503, "payment timeout") from exc
    except httpx.HTTPError as exc:
        logger.warning("payment_unavailable reference=%s", reference)
        raise PaymentError(503, "payment unavailable") from exc
    if response.status_code == 401:
        logger.warning("payment_unauthorized reference=%s", reference)
        raise PaymentError(502, "payment unauthorized")
    if response.status_code >= 500:
        logger.warning("payment_error status=%s reference=%s", response.status_code, reference)
        raise PaymentError(502, "payment failed")
    if response.status_code >= 400:
        logger.warning("payment_rejected status=%s reference=%s", response.status_code, reference)
        raise PaymentError(502, "payment rejected")
    body = response.json()
    payment_id = body.get("id", "")
    logger.info("payment_approved reference=%s id=%s", reference, payment_id)
    return str(payment_id)


def probe_payment() -> dict[str, str]:
    """Report in-process payment, or the remote stub health."""
    base = payment_base_url()
    if not base:
        return {"status": "ok", "mode": "in_process_stub"}
    try:
        response = httpx.get(f"{base}/health", timeout=2.0)
    except httpx.HTTPError:
        logger.warning("payment_health_unreachable")
        return {"status": "error", "mode": "remote"}
    if response.status_code != 200:
        logger.warning("payment_health_status %s", response.status_code)
        return {"status": "error", "mode": "remote"}
    return {"status": "ok", "mode": "remote"}
