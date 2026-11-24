from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class HttpExchange:
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
    return Path("test-results/api/exchanges")


def safe_nodeid(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nodeid)[:180]


def write_exchange(path: Path, exchange: HttpExchange) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(exchange), indent=2, default=str) + "\n", encoding="utf-8")
