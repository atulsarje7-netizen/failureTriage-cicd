"""Load captured API request/response exchanges for failed tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from triage.schemas.bundle import HttpCall


def safe_nodeid(nodeid: str) -> str:
    """Turn a pytest node id into the exchange filename stem used by API tests."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nodeid)[:180]


def load_exchange(directory: Path, test_id: str) -> HttpCall | None:
    """Load one captured exchange for a test id, if present."""
    path = directory / f"{safe_nodeid(test_id)}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return _to_call(payload)


def _to_call(payload: dict[str, Any]) -> HttpCall:
    """Convert a captured exchange JSON object into an HttpCall."""
    status = payload.get("status_code")
    latency = payload.get("latency_ms")
    return HttpCall(
        method=str(payload.get("method") or "GET"),
        url=str(payload.get("url") or ""),
        status_code=int(status) if status is not None else None,
        latency_ms=float(latency) if isinstance(latency, (int, float)) else None,
        request_body=payload.get("request_body"),
        response_body=payload.get("response_body"),
        source="api_exchange",
    )
