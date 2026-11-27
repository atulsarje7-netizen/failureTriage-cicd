"""Capture human confirm/correct feedback for later Evals ingestion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from triage.schemas.feedback import FeedbackAction, FeedbackRecord
from triage.schemas.verdict import Label, Verdict, VerdictReport


class FeedbackStore:
    """Append-only JSONL store of reviewer label feedback."""

    def __init__(self, path: str | Path) -> None:
        """Create the feedback file's parent directory if needed."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: FeedbackRecord) -> FeedbackRecord:
        """Append one feedback record and return it."""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
        return record

    def record(
        self,
        *,
        action: FeedbackAction,
        corrected_label: Label,
        verdict: Verdict | None = None,
        run_id: str | None = None,
        cluster_id: str | None = None,
        test_ids: list[str] | None = None,
        predicted_label: Label | None = None,
        reviewer: str | None = None,
        note: str | None = None,
        bundle_path: str | None = None,
    ) -> FeedbackRecord:
        """Build and persist one feedback record from CLI or API inputs."""
        versions = verdict.versions if verdict is not None else {}
        resolved_predicted = predicted_label or (verdict.label if verdict is not None else None)
        if action == "confirm" and resolved_predicted != corrected_label:
            raise ValueError("confirmed label must match the predicted label")
        if action == "correct" and resolved_predicted == corrected_label:
            raise ValueError("corrected label must differ from the predicted label")
        record = FeedbackRecord(
            feedback_id=str(uuid.uuid4()),
            action=action,
            run_id=run_id,
            cluster_id=cluster_id or (verdict.cluster_id if verdict else None),
            test_ids=test_ids or (list(verdict.test_ids) if verdict is not None else []),
            predicted_label=resolved_predicted,
            corrected_label=corrected_label,
            reviewer=reviewer,
            note=note,
            agent_version=versions.get("agent"),
            prompt_version=versions.get("prompt"),
            model=versions.get("model"),
            verdict_rule=verdict.rule if verdict is not None else None,
            bundle_path=bundle_path,
            recorded_at=_now(),
        )
        return self.append(record)

    def read_all(self) -> list[FeedbackRecord]:
        """Load every feedback record from the JSONL file."""
        if not self.path.is_file():
            return []
        records: list[FeedbackRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(FeedbackRecord.model_validate_json(line))
        return records


def find_verdict(
    report: VerdictReport,
    *,
    cluster_id: str | None = None,
    test_id: str | None = None,
) -> Verdict | None:
    """Locate a verdict by cluster id or a contained test id."""
    for verdict in report.verdicts:
        if cluster_id and verdict.cluster_id == cluster_id:
            return verdict
        if test_id and (test_id == verdict.test_id or test_id in verdict.test_ids):
            return verdict
    return None


def _now() -> str:
    """Return an UTC ISO-8601 timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()
