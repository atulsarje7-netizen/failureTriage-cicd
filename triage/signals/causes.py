"""Cause tags read from a failure message, stack, and HTTP statuses."""

from __future__ import annotations

import re

from triage.schemas.bundle import HttpCall

_STATUS_ASSERT = re.compile(r"assert\s+(\d{3})\s*==")
_STATUS_RESPONSE = re.compile(r"Response\s*\[(\d{3})")
_SHORT_TIMEOUT = re.compile(r"Timeout:\s*(\d+)ms")
_SELECTOR = re.compile(
    r"waiting for|getByTestId|getByRole|locator\.|element\(s\) not found|selector",
    re.IGNORECASE,
)
_ASSERTION = re.compile(
    r"\bassert\b|AssertionError|toHaveText|toHaveCount|toContainText|toHaveValue|toEqual",
    re.IGNORECASE,
)
_RESOLVED = re.compile(r"locator resolved to|unexpected value|Received:", re.IGNORECASE)
_STRONG_ENV = re.compile(
    "|".join(
        [
            r"connection refused",
            r"could not connect",
            r"connection failed",
            r"operationalerror",
            r"password authentication failed",
            r"does not exist",
            r"name or service not known",
            r"temporary failure in name resolution",
            r"nodename nor servname",
            r"getaddrinfo",
            r"network is unreachable",
            r"connecttimeout",
            r"connect timeout",
            r"payment unavailable",
            r"payment timeout",
            r"payment unauthorized",
            r"payment rejected",
            r"payment failed",
            r"payment_health",
            r"payment_unavailable",
            r"payment_unauthorized",
            r"service unavailable",
        ]
    ),
    re.IGNORECASE,
)
_ORDER = re.compile(r"order-dependent|predecessor did not run", re.IGNORECASE)
_RACE = re.compile(r"intermittent|\bflaky\b|race:", re.IGNORECASE)
_AUTH = re.compile(
    r"invalid credentials|unauthorized|not authenticated|password authentication failed",
    re.IGNORECASE,
)


def http_statuses(text: str, calls: list[HttpCall]) -> list[int]:
    """Collect HTTP status codes from exchanges and from assertion text."""
    found = [call.status_code for call in calls if call.status_code is not None]
    found.extend(int(match) for match in _STATUS_ASSERT.findall(text))
    found.extend(int(match) for match in _STATUS_RESPONSE.findall(text))
    return found


def cause_tags(text: str, statuses: list[int]) -> list[str]:
    """Return ordered cause tags for one failure's text and status codes."""
    tags: list[str] = []
    short = _SHORT_TIMEOUT.search(text) or re.search(r"Timeout\s+(\d+)ms", text)
    selector = _SELECTOR.search(text) is not None
    assertion = _ASSERTION.search(text) is not None
    if selector and assertion and _RESOLVED.search(text):
        selector = False
    if short and int(short.group(1)) < 1000:
        _add(tags, "timeout")
        _add(tags, "race-condition")
        selector = False
    elif re.search(r"timed out|Timeout \d+ms exceeded|payment timeout", text, re.IGNORECASE):
        if not selector:
            _add(tags, "timeout")
    if selector:
        _add(tags, "selector-not-found")
    if _ORDER.search(text):
        _add(tags, "order-dependent")
    if _RACE.search(text):
        _add(tags, "race-condition")
    if any(500 <= code <= 599 for code in statuses):
        _add(tags, "http-5xx")
    if any(400 <= code <= 499 for code in statuses):
        _add(tags, "http-4xx")
    if any(code == 401 for code in statuses) or _AUTH.search(text):
        _add(tags, "auth")
    if any(code == 503 for code in statuses) or re.search(
        r"service unavailable|payment unavailable|connection refused",
        text,
        re.IGNORECASE,
    ):
        _add(tags, "service-unavailable")
    if re.search(
        r"connection refused|could not connect|getaddrinfo|"
        r"name or service not known|operationalerror",
        text,
        re.IGNORECASE,
    ):
        _add(tags, "network")
    if assertion and "selector-not-found" not in tags and "http-5xx" not in tags:
        if "http-4xx" not in tags and "timeout" not in tags:
            _add(tags, "assertion-mismatch")
    return tags


def strong_environment(text: str) -> bool:
    """Return whether text describes a down, unreachable, or misconfigured dependency."""
    return _STRONG_ENV.search(text) is not None


def looks_flaky(tags: list[str], text: str) -> bool:
    """Return whether the failure looks intermittent and still needs a passing attempt."""
    if "order-dependent" in tags or "race-condition" in tags:
        return True
    if "timeout" in tags and "selector-not-found" not in tags and "http-5xx" not in tags:
        return True
    return _RACE.search(text) is not None


def _add(tags: list[str], tag: str) -> None:
    """Append a tag once, keeping first-seen order."""
    if tag not in tags:
        tags.append(tag)
