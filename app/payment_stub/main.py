from __future__ import annotations

import hmac
import os
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

app = FastAPI(title="Payment stub", version="0.1.0")


class ChargeIn(BaseModel):
    """Amount and client reference for a charge."""

    amount_cents: int
    reference: str


def _mode() -> str:
    """Return PAYMENT_STUB_MODE, defaulting to a healthy stub."""
    return os.environ.get("PAYMENT_STUB_MODE", "ok")


def _authorized(authorization: str | None) -> bool:
    """Return True when the bearer token matches PAYMENT_STUB_TOKEN, if one is set."""
    expected = os.environ.get("PAYMENT_STUB_TOKEN", "")
    if not expected:
        return True
    prefix = "Bearer "
    if authorization is None or not authorization.startswith(prefix):
        return False
    return hmac.compare_digest(authorization.removeprefix(prefix), expected)


@app.get("/health")
def health() -> dict[str, str]:
    """Report stub liveness. Charge faults do not fail this endpoint."""
    return {"status": "ok", "mode": _mode()}


@app.post("/charge")
def charge(
    body: ChargeIn,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    """Approve a charge, or fail according to the stub mode and token."""
    if not _authorized(authorization):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad token")
    delay_ms = int(os.environ.get("PAYMENT_STUB_DELAY_MS", "0") or "0")
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)
    mode = _mode()
    if mode == "error":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="processor error",
        )
    if mode == "down":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="down")
    return {"id": f"pay_{uuid.uuid4().hex[:12]}", "status": "approved", "reference": body.reference}
