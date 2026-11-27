"""Append-only JSONL audit log for verdicts and publish actions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from triage.schemas.audit import AuditAction, AuditEntry
from triage.schemas.report import BugReportDraft, DraftReport
from triage.schemas.verdict import Verdict, VerdictReport


class AuditLog:
    """Write durable audit entries that link verdicts to evidence and versions."""

    def __init__(self, path: str | Path) -> None:
        """Create the audit file's parent directory if needed."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: AuditEntry) -> AuditEntry:
        """Append one JSON line and return the entry."""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
        return entry

    def record_verdicts(
        self,
        report: VerdictReport,
        *,
        action: AuditAction = "classify",
        budget: dict[str, object] | None = None,
        notes: list[str] | None = None,
    ) -> list[AuditEntry]:
        """Write one audit entry per verdict from a classification pass."""
        entries: list[AuditEntry] = []
        for verdict in report.verdicts:
            entries.append(
                self.append(
                    _from_verdict(
                        verdict,
                        run_id=report.run_id,
                        action=action,
                        budget=budget or {},
                        notes=notes or [],
                    )
                )
            )
        return entries

    def record_publish(
        self,
        draft_report: DraftReport,
        *,
        issue_numbers: dict[str, int] | None = None,
        notes: list[str] | None = None,
    ) -> list[AuditEntry]:
        """Write one audit entry per published draft, including issue numbers."""
        numbers = issue_numbers or {}
        entries: list[AuditEntry] = []
        for draft in draft_report.drafts:
            entries.append(
                self.append(
                    _from_draft(
                        draft,
                        run_id=draft_report.run_id,
                        issue_number=numbers.get(draft.draft_id),
                        notes=notes or [],
                    )
                )
            )
        return entries

    def read_all(self) -> list[AuditEntry]:
        """Load every audit entry from the JSONL file."""
        if not self.path.is_file():
            return []
        entries: list[AuditEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(AuditEntry.model_validate_json(line))
        return entries


def _from_verdict(
    verdict: Verdict,
    *,
    run_id: str | None,
    action: AuditAction,
    budget: dict[str, object],
    notes: list[str],
) -> AuditEntry:
    """Build an audit entry from a verdict."""
    return AuditEntry(
        entry_id=str(uuid.uuid4()),
        action=action,
        recorded_at=_now(),
        run_id=run_id,
        cluster_id=verdict.cluster_id,
        label=verdict.label,
        confidence=verdict.confidence,
        needs_human=verdict.needs_human,
        evidence=list(verdict.evidence),
        versions=dict(verdict.versions),
        signature=verdict.signature,
        budget=budget,
        notes=notes,
    )


def _from_draft(
    draft: BugReportDraft,
    *,
    run_id: str | None,
    issue_number: int | None,
    notes: list[str],
) -> AuditEntry:
    """Build a publish audit entry from a draft bug."""
    return AuditEntry(
        entry_id=str(uuid.uuid4()),
        action="publish",
        recorded_at=_now(),
        run_id=run_id or draft.run_id,
        cluster_id=draft.cluster_id,
        draft_id=draft.draft_id,
        label=draft.label,
        confidence=draft.confidence,
        evidence=list(draft.evidence),
        versions={},
        signature=draft.signature,
        issue_number=issue_number,
        notes=notes,
    )


def _now() -> str:
    """Return an UTC ISO-8601 timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()
