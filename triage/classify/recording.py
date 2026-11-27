"""In-memory recorder for one classification pass."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from triage.schemas.trace import EventKind, RunTrace, TraceEvent


class Tracer:
    """Record pipeline steps, tool calls, and LLM calls."""

    def __init__(self) -> None:
        """Start an empty trace."""
        self.events: list[TraceEvent] = []

    @contextmanager
    def step(self, name: str, inputs: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Time one deterministic pipeline step."""
        with self._record(name, inputs, kind="step") as box:
            yield box

    @contextmanager
    def tool_call(self, name: str, inputs: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Time one evidence tool invocation."""
        with self._record(name, inputs, kind="tool") as box:
            yield box

    @contextmanager
    def llm_call(self, name: str, inputs: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Time one provider generation request."""
        with self._record(name, inputs, kind="llm") as box:
            yield box

    def finish(self, agent_version: str) -> RunTrace:
        """Return the ordered trace for this pass."""
        return RunTrace(agent_version=agent_version, events=list(self.events))

    @contextmanager
    def _record(
        self,
        name: str,
        inputs: dict[str, Any],
        *,
        kind: EventKind,
    ) -> Iterator[dict[str, Any]]:
        """Shared timing wrapper that stores output size and optional tokens."""
        box: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            yield box
        except Exception as exc:
            self._add(name, kind, inputs, {}, start, error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            output = box.get("output", {})
            payload = output if isinstance(output, dict) else {"value": output}
            tokens = box.get("tokens")
            tokens_in = None
            tokens_out = None
            if isinstance(tokens, dict):
                tokens_in = int(tokens.get("input") or 0)
                tokens_out = int(tokens.get("output") or 0)
            self._add(
                name,
                kind,
                inputs,
                payload,
                start,
                error=None,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

    def _add(
        self,
        name: str,
        kind: EventKind,
        inputs: dict[str, Any],
        output: dict[str, Any],
        start: float,
        *,
        error: str | None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> None:
        """Append one trace event."""
        encoded = json.dumps(output, default=str)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
        self.events.append(
            TraceEvent(
                name=name,
                kind=kind,
                inputs=inputs,
                result_size=len(encoded),
                latency_ms=elapsed_ms,
                error=error,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        )
