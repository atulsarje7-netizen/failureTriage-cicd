"""Unit tests for the Playwright JSON report parser."""

from __future__ import annotations

from pathlib import Path

from triage.collect.playwright_json import PlaywrightJsonParser


def test_playwright_parser_reads_flow_and_statuses(fixtures_dir: Path) -> None:
    """Playwright fixtures yield flow annotations, failures, and flaky retries."""
    results = PlaywrightJsonParser().parse(fixtures_dir / "playwright_results.json")
    by_title = {item.title: item for item in results}
    checkout = by_title["checkout places an order visible in history"]
    assert checkout.layer == "ui"
    assert checkout.flow_id == "checkout"
    assert checkout.status == "failed"
    assert checkout.message is not None
    assert any("trace.zip" in path for path in checkout.attachments)

    flaky = by_title["alice can log in and reach the catalog"]
    assert flaky.status == "flaky"
    assert flaky.flow_id == "login"
    assert len(flaky.attempts) == 2
    assert flaky.attempts[0].status == "failed"
    assert flaky.attempts[1].status == "passed"

    passed = by_title["guest can open login"]
    assert passed.status == "passed"
