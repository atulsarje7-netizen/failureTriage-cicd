"""Tests for human feedback capture."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from triage.cli import app
from triage.feedback import FeedbackStore, find_verdict
from triage.schemas.verdict import EvidenceCitation, Verdict, VerdictReport


def _report() -> VerdictReport:
    """One product_bug verdict for feedback tests."""
    return VerdictReport(
        agent_version="0.9.0",
        run_id="run-9",
        examined="examined 1 of 1",
        verdicts=[
            Verdict(
                test_id="tests/api/test_orders.py::test_checkout",
                test_ids=["tests/api/test_orders.py::test_checkout"],
                cluster_id="cluster-a",
                label="product_bug",
                confidence=0.88,
                evidence=[
                    EvidenceCitation(
                        source="test",
                        ref="tests/api/test_orders.py::test_checkout",
                        note="assertion",
                    )
                ],
                versions={"agent": "0.9.0", "prompt": "rules-v1", "model": "rules"},
                rule="layer_matrix",
            )
        ],
    )


def test_feedback_store_records_correction(tmp_path: Path) -> None:
    """Corrections are written as Evals-ingestible JSONL."""
    store = FeedbackStore(tmp_path / "feedback.jsonl")
    verdict = _report().verdicts[0]
    record = store.record(
        action="correct",
        corrected_label="test_bug",
        verdict=verdict,
        run_id="run-9",
        reviewer="alice",
        note="wrong layer",
    )
    assert record.predicted_label == "product_bug"
    assert record.corrected_label == "test_bug"
    assert record.agent_version == "0.9.0"
    loaded = store.read_all()
    assert len(loaded) == 1
    assert loaded[0].feedback_id == record.feedback_id


def test_feedback_action_must_match_label_semantics(tmp_path: Path) -> None:
    """Confirm and correct actions cannot contradict the predicted label."""
    store = FeedbackStore(tmp_path / "feedback.jsonl")
    verdict = _report().verdicts[0]
    with pytest.raises(ValueError, match="confirmed label must match"):
        store.record(action="confirm", corrected_label="test_bug", verdict=verdict)
    with pytest.raises(ValueError, match="corrected label must differ"):
        store.record(action="correct", corrected_label="product_bug", verdict=verdict)
    assert store.read_all() == []


def test_find_verdict_by_cluster_and_test() -> None:
    """Feedback lookup finds a verdict by cluster or test id."""
    report = _report()
    assert find_verdict(report, cluster_id="cluster-a") is not None
    assert find_verdict(report, test_id="tests/api/test_orders.py::test_checkout") is not None
    assert find_verdict(report, cluster_id="missing") is None


def test_feedback_cli(tmp_path: Path) -> None:
    """CLI feedback command appends a confirm record."""
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(_report().model_dump_json(indent=2) + "\n", encoding="utf-8")
    feedback_path = tmp_path / "feedback.jsonl"
    config_path = tmp_path / "triage.config.yaml"
    config_path.write_text(
        f"feedback:\n  path: {feedback_path}\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback",
            "--verdicts",
            str(verdicts),
            "--cluster",
            "cluster-a",
            "--label",
            "product_bug",
            "--confirm",
            "--reviewer",
            "bob",
            "--config",
            str(config_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert feedback_path.is_file()
    store = FeedbackStore(feedback_path)
    records = store.read_all()
    assert len(records) == 1
    assert records[0].action == "confirm"
    assert records[0].corrected_label == "product_bug"
