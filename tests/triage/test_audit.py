"""Tests for the append-only audit log."""

from __future__ import annotations

from pathlib import Path

from tests.triage.fakes import FakeWriter
from triage.audit import AuditLog
from triage.collect.assemble import build_bundle
from triage.config import AuditSettings, PublishSettings, TriageConfig
from triage.report.draft import draft_reports
from triage.report.publish import publish_drafts
from triage.schemas.bundle import RunMetadata
from triage.schemas.bundle import TestResult as EvidenceTestResult
from triage.schemas.verdict import EvidenceCitation, Verdict, VerdictReport


def test_audit_records_verdicts_with_evidence_and_versions(tmp_path: Path) -> None:
    """Every classify audit entry keeps citations and version stamps."""
    report = VerdictReport(
        agent_version="0.9.0",
        run_id="run-1",
        examined="examined 1 of 1",
        verdicts=[
            Verdict(
                test_id="tests/api/test_orders.py::test_checkout",
                test_ids=["tests/api/test_orders.py::test_checkout"],
                cluster_id="c1",
                label="product_bug",
                confidence=0.9,
                evidence=[
                    EvidenceCitation(
                        source="diff",
                        ref="app/api/orders.py",
                        note="changed",
                    )
                ],
                versions={"agent": "0.9.0", "prompt": "rules-v1", "model": "rules"},
                rule="layer_matrix",
            )
        ],
    )
    audit = AuditLog(tmp_path / "audit.jsonl")
    entries = audit.record_verdicts(report, budget={"ok": True})
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "classify"
    assert entry.label == "product_bug"
    assert entry.evidence[0].ref == "app/api/orders.py"
    assert entry.versions["agent"] == "0.9.0"
    assert audit.read_all()[0].entry_id == entry.entry_id


def test_publish_writes_audit_with_issue_number(tmp_path: Path) -> None:
    """Publish audit entries link drafts back to created issue numbers."""
    bundle = build_bundle(
        run=RunMetadata(
            run_id="run-1",
            source="local",
            repository="owner/repo",
            commit_sha="abc",
        ),
        tests=[
            EvidenceTestResult(
                test_id="tests/api/test_orders.py::test_checkout",
                layer="api",
                flow_id="checkout",
                status="failed",
                message="assert 500 == 201",
            )
        ],
        changes=[],
    )
    report = VerdictReport(
        agent_version="0.9.0",
        run_id="run-1",
        examined=bundle.examined.as_phrase(),
        verdicts=[
            Verdict(
                test_id="tests/api/test_orders.py::test_checkout",
                test_ids=["tests/api/test_orders.py::test_checkout"],
                cluster_id="c1",
                label="product_bug",
                confidence=0.91,
                evidence=[
                    EvidenceCitation(
                        source="test",
                        ref="tests/api/test_orders.py::test_checkout",
                        note="500",
                    )
                ],
                rule="layer_matrix",
            )
        ],
    )
    drafts = draft_reports(report, bundle)
    audit = AuditLog(tmp_path / "audit.jsonl")
    writer = FakeWriter()
    result = publish_drafts(
        drafts,
        config=TriageConfig(
            repository="owner/repo",
            publish=PublishSettings(pr_comment=False, draft_issue=True),
            audit=AuditSettings(enabled=True, path=str(tmp_path / "unused.jsonl")),
        ),
        writer=writer,
        audit=audit,
    )
    assert result.issues_created
    entries = audit.read_all()
    assert len(entries) == 1
    assert entries[0].action == "publish"
    assert entries[0].issue_number == result.issues_created[0]
    assert entries[0].evidence
