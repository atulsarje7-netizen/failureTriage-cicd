"""Parser for pytest JUnit XML reports."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET  # type: ignore[import-untyped]

from triage.config import FlowPathRule, resolve_flow_id
from triage.schemas.bundle import TestAttempt, TestResult, TestStatus


class JUnitParser:
    """Turn pytest JUnit XML into normalised API TestResult records."""

    layer = "api"

    def __init__(self, flow_rules: list[FlowPathRule] | None = None) -> None:
        """Optionally supply path-to-flow fallback rules."""
        self.flow_rules = flow_rules or []

    def parse(self, path: Path) -> list[TestResult]:
        """Read a JUnit XML file into normalised API test results."""
        root = ET.parse(str(path)).getroot()
        results: list[TestResult] = []
        for case in root.iter("testcase"):
            parsed = self._parse_case(case)
            if parsed is not None:
                results.append(parsed)
        return results

    def _parse_case(self, case: Element) -> TestResult | None:
        """Convert one JUnit testcase element into a TestResult."""
        name = case.attrib.get("name", "")
        if not name:
            return None
        file_name = case.attrib.get("file")
        classname = case.attrib.get("classname", "")
        file_path: str | None
        if file_name:
            test_id = f"{file_name}::{name}"
            file_path = file_name
        else:
            module = classname.replace(".", "/")
            file_path = f"{module}.py" if module else None
            test_id = f"{module}.py::{name}" if module else name

        duration_s = case.attrib.get("time")
        duration_ms = round(float(duration_s) * 1000, 3) if duration_s else None
        flow_id = self._property(case, "flow_id") or resolve_flow_id(
            test_id, file_path, self.flow_rules
        )

        skipped = case.find("skipped")
        failure = case.find("failure")
        error = case.find("error")
        status: TestStatus
        message: str | None = None
        stack: str | None = None
        if skipped is not None:
            status = "skipped"
            message = skipped.attrib.get("message") or (skipped.text or None)
        elif failure is not None:
            status = "failed"
            message = failure.attrib.get("message")
            stack = failure.text
        elif error is not None:
            status = "failed"
            message = error.attrib.get("message")
            stack = error.text
        else:
            status = "passed"

        attempts = [
            TestAttempt(
                attempt=1,
                status=status,
                duration_ms=duration_ms,
                message=message,
                stack=stack,
            )
        ]
        return TestResult(
            test_id=test_id,
            layer="api",
            flow_id=flow_id,
            status=status,
            duration_ms=duration_ms,
            message=message,
            stack=stack,
            attempts=attempts,
            file=file_path,
            title=name,
        )

    @staticmethod
    def _property(case: Element, name: str) -> str | None:
        """Read a named JUnit property from the case or its properties child."""
        for prop in case.iter("property"):
            if prop.attrib.get("name") == name:
                value = prop.attrib.get("value")
                return value if value else None
        return None
