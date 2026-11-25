"""Unit tests for the pytest JUnit XML parser."""

from __future__ import annotations

from pathlib import Path

from triage.collect.junit import JUnitParser
from triage.config import FlowPathRule


def test_junit_parser_normalises_api_results(fixtures_dir: Path) -> None:
    """JUnit fixtures yield flow_id, layer, and failure details."""
    results = JUnitParser().parse(fixtures_dir / "junit_api.xml")
    by_id = {item.test_id: item for item in results}
    failed = by_id["tests/api/test_orders.py::test_checkout_creates_order_and_clears_cart"]
    assert failed.layer == "api"
    assert failed.flow_id == "checkout"
    assert failed.status == "failed"
    assert failed.message is not None and "500" in failed.message
    skipped = by_id["tests/api/test_exchange_capture.py::test_demo_deliberate_failure"]
    assert skipped.status == "skipped"
    passed = by_id["tests/api/test_auth.py::test_login_success"]
    assert passed.status == "passed"
    assert passed.flow_id == "login"


def test_junit_parser_uses_flow_path_fallback(fixtures_dir: Path, tmp_path: Path) -> None:
    """Path rules supply flow_id when the XML has no properties."""
    xml = tmp_path / "junit.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
        <testsuites><testsuite tests="1">
          <testcase classname="tests.api.test_orders" name="test_checkout_empty_cart"
                    file="tests/api/test_orders.py" time="0.01"/>
        </testsuite></testsuites>
        """,
        encoding="utf-8",
    )
    rules = [FlowPathRule(pattern="tests/api/test_orders.py", flow_id="checkout")]
    results = JUnitParser(rules).parse(xml)
    assert len(results) == 1
    assert results[0].flow_id == "checkout"
    assert results[0].status == "passed"
