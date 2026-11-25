"""In-memory recorder for one classification pass."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from triage.schemas.trace import RunTrace, TraceEvent


class Tracer:
    """Record each pipeline step's inputs, result size, latency, and error."""

    def __init__(self) -> None:
        """Start an empty trace."""
        self.events: list[TraceEvent] = []

    @contextmanager
    def step(self, name: str, inputs: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Time one step and store whatever the body puts in output."""
        box: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            yield box
        except Exception as exc:
            self._add(name, inputs, {}, start, error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            output = box.get("output", {})
            payload = output if isinstance(output, dict) else {"value": output}
            self._add(name, inputs, payload, start, error=None)

    def finish(self, agent_version: str) -> RunTrace:
        """Return the ordered trace for this pass."""
        return RunTrace(agent_version=agent_version, events=list(self.events))

    def _add(
        self,
        name: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        start: float,
        *,
        error: str | None,
    ) -> None:
        """Append one trace event."""
        encoded = json.dumps(output, default=str)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
        self.events.append(
            TraceEvent(
                name=name,
                inputs=inputs,
                result_size=len(encoded),
                latency_ms=elapsed_ms,
                error=error,
            )
        )
