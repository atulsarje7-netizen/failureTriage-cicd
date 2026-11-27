"""Capped tool-calling loop that ends on submit_verdicts or the call budget."""

from __future__ import annotations

import json
from typing import Any

from triage.classify.recording import Tracer
from triage.llm.base import LLMClient
from triage.llm.redact import redact_text, redact_value
from triage.llm.tools import EvidenceToolkit, clip_tool_result
from triage.llm.types import ChatMessage, ToolCall


class ToolLoopResult:
    """Outcome of one capped tool loop."""

    def __init__(
        self,
        *,
        verdicts: list[dict[str, Any]] | None,
        tool_calls: int,
        capped: bool,
        error: str | None = None,
    ) -> None:
        """Store submitted verdicts or the reason the loop stopped early."""
        self.verdicts = verdicts
        self.tool_calls = tool_calls
        self.capped = capped
        self.error = error


def run_tool_loop(
    client: LLMClient,
    toolkit: EvidenceToolkit,
    messages: list[ChatMessage],
    *,
    max_tool_calls: int,
    tracer: Tracer,
) -> ToolLoopResult:
    """Call the model until submit_verdicts, the cap, or an error."""
    tools = toolkit.specs()
    calls_used = 0
    history = list(messages)
    while True:
        with tracer.llm_call(
            "llm_complete",
            {"model": client.model_name, "messages": len(history), "tools": len(tools)},
        ) as box:
            try:
                response = client.complete(history, tools)
            except Exception as exc:
                error = redact_text(f"{type(exc).__name__}: {exc}")
                box["output"] = {"error": error}
                return ToolLoopResult(
                    verdicts=None,
                    tool_calls=calls_used,
                    capped=False,
                    error=error,
                )
            box["output"] = {
                "tool_calls": [call.name for call in response.message.tool_calls],
                "has_text": bool(response.message.content),
            }
            box["tokens"] = {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
            }

        assistant = response.message
        history.append(assistant)

        if not assistant.tool_calls:
            parsed = _parse_json_verdicts(assistant.content)
            if parsed is not None:
                toolkit.submitted = parsed
                return ToolLoopResult(
                    verdicts=parsed,
                    tool_calls=calls_used,
                    capped=False,
                )
            return ToolLoopResult(
                verdicts=None,
                tool_calls=calls_used,
                capped=False,
                error="model returned no tool calls and no JSON verdicts",
            )

        for call in assistant.tool_calls:
            if calls_used >= max_tool_calls:
                return ToolLoopResult(
                    verdicts=toolkit.submitted,
                    tool_calls=calls_used,
                    capped=True,
                )
            calls_used += 1
            result = _dispatch(toolkit, call, tracer)
            history.append(
                ChatMessage(
                    role="tool",
                    name=call.name,
                    tool_call_id=call.id,
                    content=clip_tool_result(result),
                )
            )
            if call.name == "submit_verdicts" and toolkit.submitted is not None:
                return ToolLoopResult(
                    verdicts=toolkit.submitted,
                    tool_calls=calls_used,
                    capped=False,
                )


def _dispatch(toolkit: EvidenceToolkit, call: ToolCall, tracer: Tracer) -> object:
    """Execute one tool call and record it on the run trace."""
    with tracer.tool_call(call.name, redact_value(call.arguments)) as box:
        result = toolkit.call(call.name, call.arguments)
        box["output"] = {
            "result_preview": clip_tool_result(result, limit=500),
            "result_size": len(json.dumps(result, default=str)),
        }
        return result


def _parse_json_verdicts(content: str | None) -> list[dict[str, Any]] | None:
    """Accept a raw JSON verdict list if the model skipped submit_verdicts."""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.startswith("```"))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("verdicts"), list):
        payload = payload["verdicts"]
    if not isinstance(payload, list):
        return None
    return [item for item in payload if isinstance(item, dict)]
