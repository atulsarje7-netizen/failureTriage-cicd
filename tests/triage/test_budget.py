"""Tests for cost and latency budget enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from triage.budget import BudgetGuard, BudgetSettings, estimate_cost
from triage.classify.recording import Tracer
from triage.cli import app
from triage.collect.assemble import build_bundle
from triage.config import BudgetSettingsModel, TriageConfig
from triage.llm.classify import run_llm
from triage.llm.loop import run_tool_loop
from triage.llm.tools import EvidenceToolkit
from triage.llm.types import ChatMessage, LLMResponse, TokenUsage, ToolCall
from triage.schemas.bundle import RunMetadata
from triage.schemas.bundle import TestResult as EvidenceTestResult
from triage.schemas.verdict import VerdictReport


class _ScriptedClient:
    """Return canned LLM responses in order."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        """Store scripted responses."""
        self.model_name = "fake-model"
        self._responses = list(responses)
        self.calls = 0

    def complete(self, messages: list[ChatMessage], tools: list[object]) -> LLMResponse:
        """Pop the next scripted response."""
        del messages, tools
        self.calls += 1
        return self._responses.pop(0)


def _bundle() -> object:
    """Minimal failing checkout bundle for loop tests."""
    return build_bundle(
        run=RunMetadata(run_id="run-1", source="local"),
        tests=[
            EvidenceTestResult(
                test_id="tests/api/test_orders.py::test_checkout",
                layer="api",
                flow_id="checkout",
                status="failed",
                message="AssertionError",
            )
        ],
        changes=[],
    )


def test_estimate_cost() -> None:
    """Cost uses the configured per-million rates."""
    assert estimate_cost(1_000_000, 0, input_cost_per_1m=0.10, output_cost_per_1m=0.40) == 0.10
    assert estimate_cost(0, 1_000_000, input_cost_per_1m=0.10, output_cost_per_1m=0.40) == 0.40


def test_budget_guard_trips_on_tokens() -> None:
    """Token ceiling marks the status as exceeded."""
    guard = BudgetGuard(BudgetSettings(max_tokens=100, max_latency_ms=None, max_cost_usd=None))
    status = guard.add_event(tokens_in=80, tokens_out=30)
    assert status.ok is False
    assert status.exceeded == "tokens"


def test_tool_loop_stops_cleanly_when_budget_exceeded() -> None:
    """Past the token budget the loop stops without requiring a submit."""
    toolkit = EvidenceToolkit(_bundle())  # type: ignore[arg-type]
    client = _ScriptedClient(
        [
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    tool_calls=[ToolCall(id="1", name="get_diff", arguments={})],
                ),
                usage=TokenUsage(input_tokens=60, output_tokens=10),
            ),
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    tool_calls=[ToolCall(id="2", name="get_test_results", arguments={})],
                ),
                usage=TokenUsage(input_tokens=60, output_tokens=10),
            ),
        ]
    )
    tracer = Tracer()
    budget = BudgetGuard(BudgetSettings(max_tokens=50, max_latency_ms=None, max_cost_usd=None))
    result = run_tool_loop(
        client,  # type: ignore[arg-type]
        toolkit,
        [ChatMessage(role="user", content="classify")],
        max_tool_calls=10,
        tracer=tracer,
        budget=budget,
    )
    assert result.budget_exceeded == "tokens"
    assert result.error == "budget_exceeded:tokens"
    assert result.verdicts is None


def test_run_llm_abstains_on_budget() -> None:
    """LLM classify returns needs_human when the token budget is exceeded."""
    # Timeout UI failure with empty diff abstains in rules, so the model path runs.
    bundle = build_bundle(
        run=RunMetadata(run_id="run-1", source="local"),
        tests=[
            EvidenceTestResult(
                test_id="tests/ui/checkout.spec.ts > places order",
                layer="ui",
                flow_id="checkout",
                status="failed",
                message="Timeout 5000ms exceeded",
                file="tests/ui/checkout.spec.ts",
            )
        ],
    )
    client = _ScriptedClient(
        [
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    tool_calls=[ToolCall(id="1", name="get_diff", arguments={})],
                ),
                usage=TokenUsage(input_tokens=1000, output_tokens=100),
            )
        ]
    )
    config = TriageConfig(
        max_tool_calls=8,
        budgets=BudgetSettingsModel(max_tokens=50, max_latency_ms=None, max_cost_usd=None),
    )
    report, trace = run_llm(bundle, config, client)  # type: ignore[arg-type]
    assert report.verdicts
    assert report.verdicts[0].label == "needs_human"
    assert "budget_tokens" in report.verdicts[0].rule
    assert any(event.name == "llm_abstain" for event in trace.events)


def test_cli_abstains_and_skips_drafts_when_run_budget_is_exceeded(tmp_path: Path) -> None:
    """A completed but over-budget rules run cannot publish an actionable label."""
    bundle_path = tmp_path / "bundle.json"
    bundle = build_bundle(
        run=RunMetadata(run_id="budget-run", source="local"),
        tests=[
            EvidenceTestResult(
                test_id="tests/api/test_orders.py::test_checkout",
                layer="api",
                status="failed",
                message="AssertionError: expected 201",
            )
        ],
        changes=[],
    )
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")
    config_path = tmp_path / "triage.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "budgets": {"max_latency_ms": 0.000001},
                "history": {"enabled": False},
                "audit": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "verdicts.json"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--bundle",
            str(bundle_path),
            "--out",
            str(out),
            "--config",
            str(config_path),
            "--no-verify",
        ],
    )
    assert result.exit_code == 0, result.output
    classified = VerdictReport.model_validate_json(out.read_text(encoding="utf-8"))
    assert classified.verdicts
    assert all(verdict.label == "needs_human" for verdict in classified.verdicts)
    drafts = json.loads(out.with_name("drafts.json").read_text(encoding="utf-8"))
    assert not drafts["drafts"]
