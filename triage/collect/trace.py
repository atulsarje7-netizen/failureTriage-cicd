"""Extract HTTP calls from a Playwright trace.zip."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, cast

from triage.schemas.bundle import HttpCall


def _dict(value: object) -> dict[str, Any]:
    """Return value when it is a dict."""
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {}


def extract_http_calls(trace_path: Path) -> list[HttpCall]:
    """Read Network request/response pairs from a Playwright trace.zip."""
    if not trace_path.is_file():
        return []
    try:
        with zipfile.ZipFile(trace_path) as archive:
            if "test.trace" not in archive.namelist():
                return []
            text = archive.read("test.trace").decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile):
        return []
    return _parse_trace_text(text)


def _parse_trace_text(text: str) -> list[HttpCall]:
    """Pair requestWillBeSent and responseReceived events into HTTP calls."""
    pending: dict[str, dict[str, Any]] = {}
    completed: list[HttpCall] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict) or raw.get("type") != "event":
            continue
        event = cast(dict[str, Any], raw)
        params = _dict(event.get("params"))
        method = str(event.get("method") or "")
        request_id = str(params.get("requestId") or "")
        if method == "Network.requestWillBeSent":
            request = _dict(params.get("request"))
            pending[request_id] = {
                "method": str(request.get("method") or "GET"),
                "url": str(request.get("url") or ""),
            }
        elif method == "Network.responseReceived":
            response = _dict(params.get("response"))
            bucket = pending.pop(request_id, {})
            url = str(response.get("url") or bucket.get("url") or "")
            if not url:
                continue
            status = response.get("status")
            completed.append(
                HttpCall(
                    method=str(bucket.get("method") or "GET"),
                    url=url,
                    status_code=int(status) if status is not None else None,
                    source="playwright_trace",
                )
            )
    return completed
