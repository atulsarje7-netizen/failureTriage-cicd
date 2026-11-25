"""Versioned verdicts produced by the rules classifier."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VERDICT_SCHEMA_VERSION = "1"
RULES_PROMPT_VERSION = "rules-v1"

PrimaryLabel = Literal["flaky_test", "product_bug", "test_bug", "environment_issue"]
Label = Literal["flaky_test", "product_bug", "test_bug", "environment_issue", "needs_human"]
EvidenceSource = Literal["test", "trace", "diff", "log", "flow", "step", "http"]
Stage = Literal["install", "lint", "build", "test"]


class EvidenceCitation(BaseModel):
    """A pointer from a verdict back to something in the evidence bundle."""

    source: EvidenceSource
    ref: str
    note: str


class RunnerUp(BaseModel):
    """The next most plausible primary label and its uncalibrated score."""

    label: PrimaryLabel
    confidence: float


class Verification(BaseModel):
    """Rerun evidence. Same-SHA and last-green fields are filled in a later iteration."""

    same_sha_reruns: str | None = None
    last_green_sha: str | None = None
    same_run_retries: str | None = None


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
