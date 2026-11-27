"""Resolve and gate test ids against the evidence bundle."""

from __future__ import annotations

from triage.schemas.bundle import EvidenceBundle, TestResult


def bundle_test_ids(bundle: EvidenceBundle) -> set[str]:
    """Return test ids that were actually present in a test report."""
    return {test.test_id for test in bundle.tests}


def lookup_test(bundle: EvidenceBundle, test_id: str) -> TestResult | None:
    """Find a test record that was actually present in a test report."""
    for test in bundle.tests:
        if test.test_id == test_id:
            return test
    return None


def assert_allowed(test_id: str, allowed: set[str]) -> None:
    """Raise when a rerun target is not in the evidence bundle."""
    if test_id not in allowed:
        raise ValueError(f"test id not in evidence bundle: {test_id!r}")


def failed_ids_for_verdict(test_ids: list[str], bundle: EvidenceBundle) -> list[str]:
    """Keep verdict test ids that failed or flaked in the bundle, still allow-listed."""
    allowed = bundle_test_ids(bundle)
    failed_statuses = {"failed", "flaky", "timed_out", "interrupted"}
    kept: list[str] = []
    for test_id in test_ids:
        if test_id not in allowed:
            continue
        test = lookup_test(bundle, test_id)
        if test is None:
            continue
        if test.status in failed_statuses:
            kept.append(test_id)
    return kept
