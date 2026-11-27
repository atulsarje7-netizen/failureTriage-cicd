"""Human label feedback in a format the Evals framework can ingest."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from triage.schemas.verdict import Label

FEEDBACK_SCHEMA_VERSION = "1"

FeedbackAction = Literal["confirm", "correct"]


class FeedbackRecord(BaseModel):
    """One reviewer confirmation or correction of a triage label."""

    schema_version: str = FEEDBACK_SCHEMA_VERSION
    feedback_id: str
    action: FeedbackAction
    run_id: str | None = None
    cluster_id: str | None = None
    test_ids: list[str] = Field(default_factory=list)
    predicted_label: Label | None = None
    corrected_label: Label
    reviewer: str | None = None
    note: str | None = None
    agent_version: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    verdict_rule: str | None = None
    bundle_path: str | None = None
    recorded_at: str
