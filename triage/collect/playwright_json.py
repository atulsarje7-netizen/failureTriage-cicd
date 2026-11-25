"""Parser for Playwright Test JSON reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from triage.config import FlowPathRule, resolve_flow_id
from triage.schemas.bundle import TestAttempt, TestResult, TestStatus

_FAILED = frozenset({"failed", "timedOut", "interrupted"})
_STATUS_MAP: dict[str, TestStatus] = {
    "passed": "passed",
    "failed": "failed",
    "timedOut": "timed_out",
    "interrupted": "interrupted",
    "skipped": "skipped",
}


class PlaywrightJsonParser:
    """Turn a Playwright JSON report into normalised UI TestResult records."""

    layer = "ui"

    def __init__(self, flow_rules: list[FlowPathRule] | None = None) -> None:
        """Optionally supply path-to-flow fallback rules."""
        self.flow_rules = flow_rules or []

    def parse(self, path: Path) -> list[TestResult]:
        """Read a Playwright JSON report into normalised UI test results."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("playwright report must be a JSON object")
        found: list[TestResult] = []
        self._walk(payload, found)
        return found

    def _walk(self, node: dict[str, Any], found: list[TestResult]) -> None:
        """Collect specs from a nested Playwright suite tree."""
        specs = node.get("specs")
        if isinstance(specs, list):
            for spec in specs:
                if isinstance(spec, dict):
                    result = self._spec_result(spec, node)
                    if result is not None:
                        found.append(result)
        suites = node.get("suites")
        if isinstance(suites, list):
            for child in suites:
                if isinstance(child, dict):
                    self._walk(child, found)

    def _spec_result(self, spec: dict[str, Any], parent: dict[str, Any]) -> TestResult | None:
        """Build one TestResult from a Playwright spec node."""
        title = spec.get("title")
        tests = spec.get("tests")
        if not isinstance(title, str) or not isinstance(tests, list) or not tests:
            return None

        file_name = spec.get("file") or parent.get("file")
        file_path = str(file_name) if isinstance(file_name, str) else None
        suite_title = parent.get("title") if isinstance(parent.get("title"), str) else None
        test_id = f"{suite_title} > {title}" if suite_title and suite_title != title else title

        annotations = list(spec.get("annotations") or [])
        attempts: list[TestAttempt] = []
        attachments: list[str] = []
        overall: TestStatus | None = None

        for test in tests:
            if not isinstance(test, dict):
                continue
            annotations.extend(test.get("annotations") or [])
            results = test.get("results")
            if not isinstance(results, list) or not results:
                continue
            project_status = test.get("status")
            for index, raw in enumerate(results, start=1):
                if not isinstance(raw, dict):
                    continue
                raw_status = raw.get("status")
                status = _STATUS_MAP.get(str(raw_status), "failed")
                error = _first_error(raw)
                attempts.append(
                    TestAttempt(
                        attempt=index,
                        status=status,
                        duration_ms=_duration_ms(raw.get("duration")),
                        message=error[0],
                        stack=error[1],
                    )
                )
                for attachment in raw.get("attachments") or []:
                    if isinstance(attachment, dict):
                        path = attachment.get("path") or attachment.get("name")
                        if isinstance(path, str):
                            attachments.append(path)
            if isinstance(project_status, str):
                overall = _project_status(project_status, attempts)

        if not attempts:
            return None
        if overall is None:
            overall = _overall_from_attempts(attempts)

        flow_id = _flow_from_annotations(annotations) or resolve_flow_id(
            test_id, file_path, self.flow_rules
        )
        last = attempts[-1]
        return TestResult(
            test_id=test_id,
            layer="ui",
            flow_id=flow_id,
            status=overall,
            duration_ms=sum(a.duration_ms or 0 for a in attempts) or None,
            message=last.message,
            stack=last.stack,
            attempts=attempts,
            attachments=attachments,
            file=file_path,
            title=title,
        )


def _project_status(project_status: str, attempts: list[TestAttempt]) -> TestStatus:
    """Map Playwright project status onto our TestStatus values."""
    if project_status == "flaky":
        return "flaky"
    if project_status == "expected":
        return "passed"
    if project_status == "skipped":
        return "skipped"
    if project_status == "unexpected":
        last = attempts[-1].status if attempts else "failed"
        return last if last in _FAILED or last in {"timed_out", "interrupted"} else "failed"
    return _overall_from_attempts(attempts)


def _overall_from_attempts(attempts: list[TestAttempt]) -> TestStatus:
    """Derive final status from attempt history, treating fail-then-pass as flaky."""
    if not attempts:
        return "never_ran"
    statuses = [a.status for a in attempts]
    if any(s == "skipped" for s in statuses) and all(s == "skipped" for s in statuses):
        return "skipped"
    prior_failed = any(
        s in _FAILED or s in {"timed_out", "interrupted"} for s in statuses[:-1]
    )
    if statuses[-1] == "passed" and prior_failed:
        return "flaky"
    return statuses[-1]


def _flow_from_annotations(annotations: list[Any]) -> str | None:
    """Read a flow id from Playwright flow annotations."""
    for item in annotations:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "flow":
            description = item.get("description")
            if isinstance(description, str) and description:
                return description
    return None


def _first_error(result: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract the first error message and stack from a Playwright result."""
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            message = first.get("message")
            stack = first.get("stack")
            return (
                str(message) if message is not None else None,
                str(stack) if stack is not None else None,
            )
    error = result.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        stack = error.get("stack")
        return (
            str(message) if message is not None else None,
            str(stack) if stack is not None else None,
        )
    return None, None


def _duration_ms(value: Any) -> float | None:
    """Convert a Playwright duration in milliseconds to float."""
    if isinstance(value, (int, float)):
        return float(value)
    return None
