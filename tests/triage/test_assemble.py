"""Unit tests for evidence-bundle assembly and flow matrix."""

from __future__ import annotations

from pathlib import Path

from triage.collect.assemble import (
    attach_api_exchanges,
    attach_ui_traces,
    build_bundle,
    build_flow_outcomes,
)
from triage.collect.junit import JUnitParser
from triage.collect.playwright_json import PlaywrightJsonParser
from triage.schemas.bundle import RunMetadata


def test_bundle_totals_examined_and_flow_matrix(fixtures_dir: Path) -> None:
    """Combined API and UI fixtures produce examined N of M and flow outcomes."""
    api = JUnitParser().parse(fixtures_dir / "junit_api.xml")
    ui = PlaywrightJsonParser().parse(fixtures_dir / "playwright_results.json")
    attach_api_exchanges(api, fixtures_dir / "exchanges")
    # Rewrite relative attachment paths so trace lookup works from any cwd.
    for test in ui:
        test.attachments = [
            str(fixtures_dir / "traces" / "checkout-fail" / "trace.zip")
            if attachment.endswith("trace.zip")
            else attachment
            for attachment in test.attachments
        ]
    attach_ui_traces(ui, fixtures_dir / "traces")

    bundle = build_bundle(
        run=RunMetadata(run_id="fixture", source="local"),
        tests=api + ui,
    )
    assert bundle.totals.failed >= 2
    assert bundle.totals.flaky == 1
    assert bundle.totals.skipped == 1
    executed = (
        bundle.totals.passed
        + bundle.totals.failed
        + bundle.totals.flaky
        + bundle.totals.timed_out
        + bundle.totals.interrupted
    )
    assert bundle.examined.examined == executed
    assert bundle.examined.total == bundle.totals.reported
    assert "examined " in bundle.examined.as_phrase()

    flows = {flow.flow_id: flow for flow in bundle.flows}
    # Empty-cart API test passes while checkout-create fails → mixed on the API layer.
    assert flows["checkout"].api == "mixed"
    assert flows["checkout"].ui == "failed"
    assert any(call.status_code == 500 for call in flows["checkout"].ui_http_statuses)

    checkout_api = next(
        t for t in bundle.tests if t.test_id.endswith("test_checkout_creates_order_and_clears_cart")
    )
    assert checkout_api.http_calls
    assert checkout_api.http_calls[0].status_code == 500


def test_flow_outcome_mixed_when_layer_has_pass_and_fail() -> None:
    """A flow is mixed when some tests pass and others fail on the same layer."""
    from triage.schemas.bundle import TestResult

    tests = [
        TestResult(test_id="a", layer="api", flow_id="cart", status="passed"),
        TestResult(test_id="b", layer="api", flow_id="cart", status="failed"),
        TestResult(test_id="c", layer="ui", flow_id="cart", status="passed"),
    ]
    outcomes = {flow.flow_id: flow for flow in build_flow_outcomes(tests)}
    assert outcomes["cart"].api == "mixed"
    assert outcomes["cart"].ui == "passed"
