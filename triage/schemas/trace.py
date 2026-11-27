"""Versioned trace of one classification pass over an evidence bundle."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TRACE_SCHEMA_VERSION = "1"

EventKind = Literal["step", "tool", "llm"]


class TraceEvent(BaseModel):
    """One pipeline step, tool call, or LLM call with size, latency, and optional tokens."""

    name: str
    kind: EventKind = "step"
    inputs: dict[str, Any] = Field(default_factory=dict)
    result_size: int
    latency_ms: float
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class RunTrace(BaseModel):
    """Ordered events recorded while classifying one bundle."""

    schema_version: str = TRACE_SCHEMA_VERSION
    agent_version: str
    events: list[TraceEvent] = Field(default_factory=list)
