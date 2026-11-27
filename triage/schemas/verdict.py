"""Versioned verdicts produced by the rules and LLM classifiers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VERDICT_SCHEMA_VERSION = "1"
RULES_PROMPT_VERSION = "rules-v1"

PrimaryLabel = Literal[
    "flaky_test",
    "product_bug",
    "test_bug",
    "environment_issue",
    "build_break",
]
Label = Literal[
    "flaky_test",
    "product_bug",
    "test_bug",
    "environment_issue",
    "build_break",
    "needs_human",
]
EvidenceSource = Literal["test", "trace", "diff", "log", "flow", "step", "http"]
Stage = Literal["install", "lint", "build", "test"]
RerunOutcome = Literal["passed", "failed", "error", "skipped"]


class EvidenceCitation(BaseModel):
    """A pointer from a verdict back to something in the evidence bundle."""

    source: EvidenceSource
    ref: str
    note: str


class RunnerUp(BaseModel):
    """The next most plausible primary label and its uncalibrated score."""

    label: PrimaryLabel
    confidence: float


class RerunAttempt(BaseModel):
    """One allow-listed rerun of a single test id."""

    test_id: str
    sha: str | None = None
    outcome: RerunOutcome
    detail: str | None = None


class Verification(BaseModel):
    """Rerun evidence from the same run, same SHA, and last green SHA."""

    same_sha_reruns: str | None = None
    last_green_sha: str | None = None
    same_run_retries: str | None = None
    last_green_commit: str | None = None
    same_sha_attempts: list[RerunAttempt] = Field(default_factory=list)
    last_green_attempts: list[RerunAttempt] = Field(default_factory=list)
    skipped_reason: str | None = None


class ConfidenceInputs(BaseModel):
    """Recorded inputs that produced Verdict.confidence, for later calibration."""

    rule_confidence: float
    rule_agreement: float
    same_sha_pass_rate: float | None = None
    last_green_passed: bool | None = None
    sample_agreement: float
    rerun_support: float | None = None
    weights: dict[str, float] = Field(default_factory=dict)
    components: dict[str, float] = Field(default_factory=dict)
    raw_score: float


class Verdict(BaseModel):
    """One label for a failure cluster, or a single whole-run outage."""

    schema_version: str = VERDICT_SCHEMA_VERSION
    test_id: str
    test_ids: list[str] = Field(default_factory=list)
    cluster_id: str
    label: Label
    confidence: float
    runner_up: RunnerUp | None = None
    tags: list[str] = Field(default_factory=list)
    stage: Stage = "test"
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    verification: Verification | None = None
    confidence_inputs: ConfidenceInputs | None = None
    needs_human: bool = False
    versions: dict[str, str] = Field(default_factory=dict)
    signature: str | None = None
    flow_id: str | None = None
    rule: str


class VerdictReport(BaseModel):
    """Every cluster verdict for one evidence bundle."""

    schema_version: str = VERDICT_SCHEMA_VERSION
    agent_version: str
    run_id: str | None = None
    examined: str
    verdicts: list[Verdict] = Field(default_factory=list)
