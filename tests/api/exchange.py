from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class HttpExchange:
    """One recorded HTTP request and response from an API test."""
    method: str
    url: str
    request_headers: dict[str, str]
    request_body: Any
    status_code: int | None
    response_headers: dict[str, str]
    response_body: Any
    latency_ms: float
    test_id: str | None = None


def exchange_dir() -> Path:
    """Directory for captured failed-test exchanges."""
    return Path("test-results/api/exchanges")


def safe_nodeid(nodeid: str) -> str:
    """Turn a pytest node id into a safe filename stem."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nodeid)[:180]


_REDACT_HEADERS = {"authorization", "cookie", "set-cookie"}
_REDACT_KEYS = {"password", "token"}


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip credential headers from a captured exchange."""
    return {
        key: ("[redacted]" if key.lower() in _REDACT_HEADERS else value)
        for key, value in headers.items()
    }


def _redact_body(body: Any) -> Any:
    """Strip password and token fields from JSON bodies."""
    if isinstance(body, dict):
        return {
            key: ("[redacted]" if key.lower() in _REDACT_KEYS else _redact_body(value))
            for key, value in body.items()
        }
    if isinstance(body, list):
        return [_redact_body(item) for item in body]
    return body


def write_exchange(path: Path, exchange: HttpExchange) -> None:
    """Write the exchange as indented JSON with secrets redacted."""
    payload = asdict(exchange)
    payload["request_headers"] = _redact_headers(payload["request_headers"])
    payload["response_headers"] = _redact_headers(payload["response_headers"])
    payload["request_body"] = _redact_body(payload["request_body"])
    payload["response_body"] = _redact_body(payload["response_body"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
