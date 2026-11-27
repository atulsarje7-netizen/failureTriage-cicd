"""Versioned per-test history and flakiness summaries for the SQLite store."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

HISTORY_SCHEMA_VERSION = "1"

HistoryOutcome = Literal["passed", "failed", "flaky", "skipped", "other"]


class TestOutcomeRecord(BaseModel):
    """One observed outcome for a test id in a single CI run."""

    schema_version: str = HISTORY_SCHEMA_VERSION
    test_id: str
    run_id: str
    commit_sha: str | None = None
    branch: str | None = None
    outcome: HistoryOutcome
    signature: str | None = None
    recorded_at: str


class FlakinessScore(BaseModel):
    """Cross-run flakiness for one test: min(passes, failures) / total runs."""

    schema_version: str = HISTORY_SCHEMA_VERSION
    test_id: str
    passes: int
    failures: int
    total_runs: int
    score: float
    recommend_quarantine: bool = False


class SignatureRecurrence(BaseModel):
    """How often a normalised failure signature has been seen."""

    schema_version: str = HISTORY_SCHEMA_VERSION
    signature: str
    sightings: int
    last_run_id: str | None = None
    last_seen_at: str | None = None
