"""Normalise failure signatures so the same cause clusters together."""

from __future__ import annotations

import re
from pathlib import Path

from triage.schemas.bundle import TestResult, TestStatus

FAILED_STATUSES: frozenset[TestStatus] = frozenset({"failed", "timed_out", "interrupted"})
EXECUTED_STATUSES: frozenset[TestStatus] = frozenset(
    {"passed", "failed", "flaky", "timed_out", "interrupted"}
)

_EXCEPTION = re.compile(r"([A-Za-z_][\w.]*(?:Error|Exception))")
_PY_FUNC = re.compile(r'File "([^"]+)", line \d+, in (\w+)')
_PY_LINE = re.compile(r"([\w./\\-]+\.py):\d+:")
_JS_FRAME = re.compile(r"\bat\s+(?:.+/)?([^/\\]+\.(?:ts|js|py)):\d+")
_SKIP = ("site-packages", "node_modules", "playwright/lib")


def failure_text(test: TestResult) -> str:
    """Return the message and stack from failed attempts, ignoring a later pass."""
    parts: list[str] = []
    for attempt in test.attempts:
        if attempt.status not in FAILED_STATUSES:
            continue
        if attempt.message:
            parts.append(attempt.message)
        if attempt.stack:
            parts.append(attempt.stack)
    if not parts:
        if test.message:
            parts.append(test.message)
        if test.stack:
            parts.append(test.stack)
    return "\n".join(parts)


def recovered(test: TestResult) -> bool:
    """Return whether a failed attempt was followed by a pass in this run."""
    if test.status == "flaky":
        return True
    failed = any(attempt.status in FAILED_STATUSES for attempt in test.attempts)
    passed = any(attempt.status == "passed" for attempt in test.attempts)
    return bool(failed and passed and test.status == "passed")


def never_passed(test: TestResult) -> bool:
    """Return whether this run has no passing attempt for the test."""
    if test.status in {"passed", "flaky"}:
        return False
    return not any(attempt.status == "passed" for attempt in test.attempts)


def signature_parts(test: TestResult) -> tuple[str, str, str]:
    """Return the signature, exception type, and frame with line numbers removed."""
    text = failure_text(test)
    exception = exception_type(text)
    frame = frame_for(test, text)
    return f"{exception}|{frame}", exception, frame


def exception_type(text: str) -> str:
    """Return the exception name, treating a bare assert as AssertionError."""
    if "AssertionError" in text or re.search(r"\bassert\b", text):
        return "AssertionError"
    match = _EXCEPTION.search(text)
    if match:
        return match.group(1).split(".")[-1]
    if "Timeout" in text or "timed out" in text.lower():
        return "TimeoutError"
    return "Failure"


def frame_for(test: TestResult, text: str) -> str:
    """Return the first meaningful stack frame, else the test file and title."""
    found = _first_frame(text)
    if found:
        return found
    if test.file:
        return f"{Path(test.file).name}:{test.title or test.test_id}"
    return test.test_id


def _first_frame(text: str) -> str | None:
    """Return the first project frame in a pytest or Playwright stack."""
    for match in _PY_FUNC.finditer(text):
        path, func = match.group(1), match.group(2)
        if _library(path):
            continue
        return f"{Path(path).name}:{func}"
    for match in _PY_LINE.finditer(text):
        path = match.group(1)
        if _library(path):
            continue
        return Path(path).name
    for match in _JS_FRAME.finditer(text):
        name = match.group(1)
        if _library(name):
            continue
        return name
    return None


def _library(path: str) -> bool:
    """Return whether a frame path is third-party code."""
    lowered = path.replace("\\", "/")
    return any(token in lowered for token in _SKIP)
