"""Tests for the SQLite history store and flakiness scoring."""

from __future__ import annotations

from pathlib import Path

from triage.collect.assemble import build_bundle
from triage.report.draft import draft_reports
from triage.schemas.bundle import RunMetadata
from triage.schemas.bundle import TestResult as EvidenceTestResult
from triage.schemas.verdict import EvidenceCitation, Verdict, VerdictReport
from triage.signals.signature import signature_parts
from triage.store.history import HistoryStore, flakiness_score


def test_flakiness_score_formula() -> None:
    """Score is min(passes, failures) / total runs."""
    assert flakiness_score(7, 3) == 0.3
    assert flakiness_score(0, 10) == 0.0
    assert flakiness_score(5, 5) == 0.5
    assert flakiness_score(0, 0) == 0.0


def test_history_records_outcomes_and_scores(tmp_path: Path) -> None:
    """Recording multiple runs yields a flakiness score and signature recurrence."""
    store = HistoryStore(tmp_path / "history.db")
    test_id = "tests/api/test_orders.py::test_checkout"
    for index, status in enumerate(["passed", "failed", "passed", "failed", "flaky"], start=1):
        bundle = build_bundle(
            run=RunMetadata(run_id=f"run-{index}", source="local", commit_sha=f"sha{index}"),
            tests=[
                EvidenceTestResult(
                    test_id=test_id,
                    layer="api",
                    flow_id="checkout",
                    status=status,  # type: ignore[arg-type]
                    message="AssertionError" if status != "passed" else None,
                    stack='File "x.py", line 1\nAssertionError' if status == "failed" else None,
                )
            ],
        )
        store.record_bundle(bundle)
    score = store.score_for_test(test_id, quarantine_at=0.3)
    assert score.total_runs == 5
    assert score.passes == 3  # passed + passed + flaky
    assert score.failures == 3  # failed + failed + flaky
    assert score.score == 0.6
    assert score.recommend_quarantine is True
    outcomes = store.outcomes_for_test(test_id)
    assert len(outcomes) == 5
    signatures = {outcome.signature for outcome in outcomes if outcome.signature}
    assert len(signatures) == 1
    assert store.signature_recurrence(signatures.pop() or "").sightings == 2
    store.close()


def test_signature_recurrence_counts_once_per_run(tmp_path: Path) -> None:
    """Replaying one bundle or seeing the same signature across tests counts once per run."""
    test_id = "tests/api/test_orders.py::test_checkout"
    first_test = EvidenceTestResult(
        test_id=test_id,
        layer="api",
        status="failed",
        message="AssertionError: mismatch",
        stack='File "orders.py", line 10\nAssertionError: mismatch',
    )
    second_test = first_test.model_copy(
        update={"test_id": "tests/api/test_orders.py::test_checkout_again"}
    )
    signature = signature_parts(first_test)[0]
    assert signature
    store = HistoryStore(tmp_path / "history.db")
    for run_id in ("run-1", "run-1", "run-2"):
        bundle = build_bundle(
            run=RunMetadata(run_id=run_id, source="local"),
            tests=[first_test, second_test],
        )
        store.record_bundle(bundle)
    assert store.signature_recurrence(signature).sightings == 2
    store.close()


def test_draft_recommends_quarantine_without_acting(tmp_path: Path) -> None:
    """Drafts may recommend quarantine; publishable flaky stays skipped only."""
    store = HistoryStore(tmp_path / "history.db")
    test_id = "tests/api/test_orders.py::test_checkout"
    for index, status in enumerate(["passed", "failed", "passed", "failed"], start=1):
        store.record_bundle(
            build_bundle(
                run=RunMetadata(run_id=f"run-{index}", source="local"),
                tests=[
                    EvidenceTestResult(
                        test_id=test_id,
                        layer="api",
                        flow_id="checkout",
                        status=status,  # type: ignore[arg-type]
                        message="boom" if status == "failed" else None,
                    )
                ],
            )
        )
    bundle = build_bundle(
        run=RunMetadata(run_id="run-new", source="local", commit_sha="abc"),
        tests=[
            EvidenceTestResult(
                test_id=test_id,
                layer="api",
                flow_id="checkout",
                status="failed",
                message="boom",
            )
        ],
    )
    report = VerdictReport(
        agent_version="0.9.0",
        run_id="run-new",
        examined="examined 1 of 1",
        verdicts=[
            Verdict(
                test_id=test_id,
                test_ids=[test_id],
                cluster_id="c1",
                label="flaky_test",
                confidence=0.9,
                evidence=[EvidenceCitation(source="test", ref=test_id, note="mixed history")],
                rule="history",
            )
        ],
    )
    drafts = draft_reports(report, bundle, history=store, quarantine_at=0.3)
    assert drafts.drafts == []
    assert len(drafts.skipped) == 1
    skipped = drafts.skipped[0]
    assert skipped.flakiness_score == 0.5
    assert skipped.quarantine_recommendation is not None
    assert "does not quarantine" in skipped.quarantine_recommendation
    assert "Consider quarantining" in skipped.markdown
    store.close()
