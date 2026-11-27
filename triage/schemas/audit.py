"""Append-only audit entries that link published verdicts back to evidence."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from triage.schemas.verdict import EvidenceCitation, Label

AUDIT_SCHEMA_VERSION = "1"

AuditAction = Literal["classify", "publish", "budget_stop"]


class AuditEntry(BaseModel):
    """One durable record of a verdict (and optional publish) with versions."""

    schema_version: str = AUDIT_SCHEMA_VERSION
    entry_id: str
    action: AuditAction
    recorded_at: str
    run_id: str | None = None
    cluster_id: str | None = None
    draft_id: str | None = None
    label: Label | None = None
    confidence: float | None = None
    needs_human: bool = False
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    signature: str | None = None
    issue_number: int | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
