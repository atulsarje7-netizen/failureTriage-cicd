"""Unit tests for the smoke-check comparison, without executing scenarios."""

from __future__ import annotations

from triage.smoke import judge_labels


def test_judge_labels_matches_abstains_and_mismatches() -> None:
    """A full match, an all-abstain row, and a mixed row are distinct."""
    assert judge_labels("product_bug", ["product_bug", "product_bug"]) == "match"
    assert judge_labels("flaky_test", ["needs_human"]) == "abstain"
    assert judge_labels("environment_issue", ["environment_issue", "product_bug"]) == "mismatch"
    assert judge_labels("test_bug", []) == "no_verdict"
