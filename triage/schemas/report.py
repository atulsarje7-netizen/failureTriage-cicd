"""Versioned bug-report drafts rendered from clustered verdicts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from triage.schemas.verdict import EvidenceCitation, Label, Stage

REPORT_SCHEMA_VERSION = "1"

Severity = Literal["critical", "high", "medium", "low"]
DraftSkipReason = Literal[
    "needs_human",
    "build_break",
    "below_threshold",
    "flaky_recommendation_only",
]


class BugReportDraft(BaseModel):
    """One self-sufficient draft bug for a failure cluster."""

    schema_version: str = REPORT_SCHEMA_VERSION
    draft_id: str
    cluster_id: str
    title: str
    label: Label
    confidence: float
    tags: list[str] = Field(default_factory=list)
    severity: Severity
    stage: Stage = "test"
    run_id: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    repository: str | None = None
    environment: str | None = None
    signature: str | None = None
    affected_tests: list[str] = Field(default_factory=list)
    failing_step: str | None = None
    expected: str | None = None
    actual: str | None = None
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    suspect_commit: str | None = None
    suspect_files: list[str] = Field(default_factory=list)
    reproduction: str | None = None
    suggested_fix: str | None = None
    flakiness_score: float | None = None
    quarantine_recommendation: str | None = None
    markdown: str = ""
    publishable: bool = True
    skip_reason: DraftSkipReason | None = None


class DraftReport(BaseModel):
    """Every draft produced for one classified evidence bundle."""

    schema_version: str = REPORT_SCHEMA_VERSION
    agent_version: str
    run_id: str | None = None
    examined: str
    drafts: list[BugReportDraft] = Field(default_factory=list)
    skipped: list[BugReportDraft] = Field(default_factory=list)
