"""Versioned trace of one rules pass over an evidence bundle."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

TRACE_SCHEMA_VERSION = "1"


class TraceEvent(BaseModel):
    """One pipeline step with its inputs, result size, latency, and error."""

    name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    result_size: int
    latency_ms: float
    error: str | None = None


class RunTrace(BaseModel):
    """Ordered steps recorded while classifying one bundle."""

    schema_version: str = TRACE_SCHEMA_VERSION
    agent_version: str
    events: list[TraceEvent] = Field(default_factory=list)
