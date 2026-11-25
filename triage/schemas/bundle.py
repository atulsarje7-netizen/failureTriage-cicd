"""Pydantic models for a versioned CI evidence bundle."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

FileKind = Literal[
    "product",
    "product_ui",
    "test",
    "config",
    "infra",
    "dependency",
    "other",
]
TestStatus = Literal[
    "passed",
    "failed",
    "skipped",
    "flaky",
    "timed_out",
    "interrupted",
    "never_ran",
]
Layer = Literal["api", "ui"]
LayerOutcome = Literal["passed", "failed", "mixed", "not_run"]


class HttpCall(BaseModel):
    """One HTTP request/response observed in a test or Playwright trace."""

    method: str
    url: str
    status_code: int | None = None
    latency_ms: float | None = None
    request_body: Any = None
    response_body: Any = None
    source: Literal["api_exchange", "playwright_trace"] = "api_exchange"


class TestAttempt(BaseModel):
    """One retry attempt for a UI or API test."""

    attempt: int
    status: TestStatus
    duration_ms: float | None = None
    message: str | None = None
    stack: str | None = None


class TestResult(BaseModel):
    """Normalised test result shared by API and UI parsers."""

    test_id: str
    layer: Layer
    flow_id: str | None = None
    status: TestStatus
    duration_ms: float | None = None
    message: str | None = None
    stack: str | None = None
    attempts: list[TestAttempt] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    http_calls: list[HttpCall] = Field(default_factory=list)
    file: str | None = None
    title: str | None = None


class FlowOutcome(BaseModel):
    """Per-flow API and UI pass/fail summary for the layer matrix."""

    flow_id: str
    api: LayerOutcome = "not_run"
    ui: LayerOutcome = "not_run"
    ui_http_statuses: list[HttpCall] = Field(default_factory=list)
    api_test_ids: list[str] = Field(default_factory=list)
    ui_test_ids: list[str] = Field(default_factory=list)


class RunTotals(BaseModel):
    """Counts taken from the run's own report totals."""

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    flaky: int = 0
    never_ran: int = 0
    timed_out: int = 0
    interrupted: int = 0

    @property
    def reported(self) -> int:
        """Count every test that appears in a report or never-ran list."""
        return (
            self.passed
            + self.failed
            + self.skipped
            + self.flaky
            + self.never_ran
            + self.timed_out
            + self.interrupted
        )


class ExaminedCount(BaseModel):
    """How many tests were executed out of how many were expected or reported."""

    examined: int
    total: int

    def as_phrase(self) -> str:
        """Return an examined-N-of-M phrase for reports."""
        return f"examined {self.examined} of {self.total}"


class PipelineStep(BaseModel):
    """One CI job step with its outcome and optional log excerpt."""

    job_name: str
    name: str
    number: int | None = None
    conclusion: str | None = None
    status: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    log_excerpt: str | None = None


class FileChange(BaseModel):
    """One file in the diff, classed from triage.config.yaml path rules."""

    path: str
    kind: FileKind
    added_lines: int = 0
    removed_lines: int = 0


class RunMetadata(BaseModel):
    """Identifying fields for the CI run that produced this bundle."""

    run_id: str | None = None
    workflow: str | None = None
    workflow_id: int | None = None
    job_ids: list[int] = Field(default_factory=list)
    branch: str | None = None
    commit_sha: str | None = None
    event: str | None = None
    attempt: int | None = None
    html_url: str | None = None
    conclusion: str | None = None
    status: str | None = None
    runner_labels: list[str] = Field(default_factory=list)
    repository: str | None = None
    source: Literal["github_actions", "local"] = "local"


class EvidenceBundle(BaseModel):
    """Saved evidence for one CI run, ready for rules and classifiers."""

    schema_version: str = SCHEMA_VERSION
    agent_version: str
    run: RunMetadata
    steps: list[PipelineStep] = Field(default_factory=list)
    tests: list[TestResult] = Field(default_factory=list)
    never_ran: list[TestResult] = Field(default_factory=list)
    flows: list[FlowOutcome] = Field(default_factory=list)
    totals: RunTotals = Field(default_factory=RunTotals)
    examined: ExaminedCount = Field(default_factory=lambda: ExaminedCount(examined=0, total=0))
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    logs: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    changes: list[FileChange] = Field(default_factory=list)
