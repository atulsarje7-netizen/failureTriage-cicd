"""Strip secrets and token-like strings before any model call."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(authorization|api[_-]?key|token|secret|password)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+=/]+"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"),
    re.compile(r"\bAQ\.[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
)

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key)"
)


def redact_text(text: str) -> str:
    """Return text with common secret shapes replaced."""
    redacted = text
    for pattern in _PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_value(value: Any) -> Any:
    """Redact every string leaf in a nested tool result."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _SENSITIVE_KEY.search(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value
