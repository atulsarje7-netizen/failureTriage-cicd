"""Unit tests for Playwright trace HTTP extraction."""

from __future__ import annotations

from pathlib import Path

from triage.collect.trace import extract_http_calls


def test_extract_http_calls_from_trace_zip(fixtures_dir: Path) -> None:
    """A synthetic checkout trace exposes POST /checkout as HTTP 500."""
    calls = extract_http_calls(fixtures_dir / "traces" / "checkout-fail" / "trace.zip")
    assert calls
    checkout = next(call for call in calls if call.url.endswith("/checkout"))
    assert checkout.method == "POST"
    assert checkout.status_code == 500
    assert checkout.source == "playwright_trace"
    products = next(call for call in calls if call.url.endswith("/products"))
    assert products.status_code == 200
