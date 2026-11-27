"""Cost and latency budgets that stop a triage run cleanly when exceeded."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from triage.classify.recording import Tracer
from triage.schemas.trace import RunTrace


@dataclass
class BudgetSettings:
    """Per-run limits; None means that dimension is unlimited."""

    max_latency_ms: float | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    input_cost_per_1m: float = 0.10
    output_cost_per_1m: float = 0.40


@dataclass
class BudgetUsage:
    """Accumulated latency, tokens, and estimated USD for one run."""

    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    @property
    def tokens(self) -> int:
        """Total tokens seen so far."""
        return self.tokens_in + self.tokens_out


@dataclass
class BudgetStatus:
    """Whether the run is still within budget, and which limit tripped if not."""

    ok: bool = True
    exceeded: str | None = None
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    limits: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Serialize for audit and trace payloads."""
        return {
            "ok": self.ok,
            "exceeded": self.exceeded,
            "usage": {
                "latency_ms": self.usage.latency_ms,
                "tokens_in": self.usage.tokens_in,
                "tokens_out": self.usage.tokens_out,
                "tokens": self.usage.tokens,
                "cost_usd": round(self.usage.cost_usd, 6),
            },
            "limits": self.limits,
        }


class BudgetGuard:
    """Track usage against configured limits and report the first breach."""

    def __init__(self, settings: BudgetSettings | None = None) -> None:
        """Start with empty usage against the given settings."""
        self.settings = settings or BudgetSettings()
        self.usage = BudgetUsage()

    def limits_dict(self) -> dict[str, Any]:
        """Expose configured limits for audit/trace payloads."""
        return {
            "max_latency_ms": self.settings.max_latency_ms,
            "max_tokens": self.settings.max_tokens,
            "max_cost_usd": self.settings.max_cost_usd,
        }

    def add_event(
        self,
        *,
        latency_ms: float = 0.0,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> BudgetStatus:
        """Accumulate one event and return whether budgets still hold."""
        self.usage.latency_ms += max(0.0, latency_ms)
        if tokens_in:
            self.usage.tokens_in += max(0, tokens_in)
        if tokens_out:
            self.usage.tokens_out += max(0, tokens_out)
        self.usage.cost_usd = estimate_cost(
            self.usage.tokens_in,
            self.usage.tokens_out,
            input_cost_per_1m=self.settings.input_cost_per_1m,
            output_cost_per_1m=self.settings.output_cost_per_1m,
        )
        return self.status()

    def sync_from_tracer(self, tracer: Tracer) -> BudgetStatus:
        """Rebuild usage from every event already recorded on the tracer."""
        return self.sync_from_trace(RunTrace(agent_version="", events=list(tracer.events)))

    def sync_from_trace(self, trace: RunTrace) -> BudgetStatus:
        """Rebuild usage from a finished run trace."""
        self.usage = BudgetUsage()
        for event in trace.events:
            self.add_event(
                latency_ms=event.latency_ms,
                tokens_in=event.tokens_in,
                tokens_out=event.tokens_out,
            )
        return self.status()

    def status(self) -> BudgetStatus:
        """Return current usage and the first exceeded limit, if any."""
        settings = self.settings
        exceeded: str | None = None
        if settings.max_latency_ms is not None and self.usage.latency_ms > settings.max_latency_ms:
            exceeded = "latency"
        elif settings.max_tokens is not None and self.usage.tokens > settings.max_tokens:
            exceeded = "tokens"
        elif settings.max_cost_usd is not None and self.usage.cost_usd > settings.max_cost_usd:
            exceeded = "cost"
        return BudgetStatus(
            ok=exceeded is None,
            exceeded=exceeded,
            usage=BudgetUsage(
                latency_ms=self.usage.latency_ms,
                tokens_in=self.usage.tokens_in,
                tokens_out=self.usage.tokens_out,
                cost_usd=self.usage.cost_usd,
            ),
            limits=self.limits_dict(),
        )


def estimate_cost(
    tokens_in: int,
    tokens_out: int,
    *,
    input_cost_per_1m: float,
    output_cost_per_1m: float,
) -> float:
    """Estimate USD cost from token counts and per-million rates."""
    return (tokens_in / 1_000_000.0) * input_cost_per_1m + (
        tokens_out / 1_000_000.0
    ) * output_cost_per_1m
