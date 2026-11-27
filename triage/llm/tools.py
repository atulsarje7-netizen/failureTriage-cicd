"""Evidence tools the LLM may call against one saved bundle."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from triage.llm.redact import redact_text, redact_value
from triage.llm.types import ToolSpec
from triage.schemas.bundle import EvidenceBundle, TestResult

FAILED = frozenset({"failed", "timed_out", "interrupted"})
TOOL_RESULT_CHARS = 12_000
LOG_CONTEXT_LINES = 40


Handler = Callable[[dict[str, Any]], object]


class EvidenceToolkit:
    """Bind tool specs and handlers to one evidence bundle."""

    def __init__(
        self,
        bundle: EvidenceBundle,
        *,
        rule_summary: str = "",
        allowed_test_ids: set[str] | None = None,
    ) -> None:
        """Remember the bundle and optional rules hint text."""
        self.bundle = bundle
        self.rule_summary = rule_summary
        self.allowed_test_ids = allowed_test_ids
        self._handlers: dict[str, Handler] = {
            "get_test_results": self._get_test_results,
            "get_diff": self._get_diff,
            "get_flow_summary": self._get_flow_summary,
            "get_pipeline_steps": self._get_pipeline_steps,
            "get_app_logs": self._get_app_logs,
            "read_test_detail": self._read_test_detail,
            "get_rule_signals": self._get_rule_signals,
            "submit_verdicts": self._submit_verdicts,
        }
        self.submitted: list[dict[str, Any]] | None = None

    def specs(self) -> list[ToolSpec]:
        """Return the tool declarations for the model."""
        return [
            ToolSpec(
                name="get_test_results",
                description="List normalised tests, optionally only failures.",
                parameters={
                    "type": "object",
                    "properties": {
                        "failed_only": {
                            "type": "boolean",
                            "description": "When true, return failed/flaky/timed_out only.",
                        }
                    },
                },
            ),
            ToolSpec(
                name="get_diff",
                description="Return classed file changes from the evidence bundle.",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="get_flow_summary",
                description="Return per-flow API vs UI outcomes for the layer matrix.",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="get_pipeline_steps",
                description="Return CI step names and conclusions.",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="get_app_logs",
                description="Return redacted log excerpts. Prefer failing-step surroundings.",
                parameters={
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Log key from the bundle (default: first log).",
                        },
                        "needle": {
                            "type": "string",
                            "description": "Optional substring to center the excerpt on.",
                        },
                    },
                },
            ),
            ToolSpec(
                name="read_test_detail",
                description="Return one test's message, stack, attempts, and HTTP calls.",
                parameters={
                    "type": "object",
                    "properties": {
                        "test_id": {"type": "string", "description": "Exact test id."},
                    },
                    "required": ["test_id"],
                },
            ),
            ToolSpec(
                name="get_rule_signals",
                description="Return deterministic rule-signal hints already computed.",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="submit_verdicts",
                description="Submit the final list of verdicts and end the loop.",
                parameters={
                    "type": "object",
                    "properties": {
                        "verdicts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "test_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "label": {
                                        "type": "string",
                                        "enum": [
                                            "flaky_test",
                                            "product_bug",
                                            "test_bug",
                                            "environment_issue",
                                            "build_break",
                                            "needs_human",
                                        ],
                                    },
                                    "confidence": {"type": "number"},
                                    "tags": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "flow_id": {"type": "string"},
                                    "stage": {
                                        "type": "string",
                                        "enum": ["install", "lint", "build", "test"],
                                    },
                                    "signature": {"type": "string"},
                                    "evidence": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "source": {
                                                    "type": "string",
                                                    "enum": [
                                                        "test",
                                                        "trace",
                                                        "diff",
                                                        "log",
                                                        "flow",
                                                        "step",
                                                        "http",
                                                    ],
                                                },
                                                "ref": {"type": "string"},
                                                "note": {"type": "string"},
                                            },
                                            "required": ["source", "ref", "note"],
                                        },
                                    },
                                    "needs_human": {"type": "boolean"},
                                    "rationale": {"type": "string"},
                                },
                                "required": ["test_ids", "label", "confidence", "evidence"],
                            },
                        }
                    },
                    "required": ["verdicts"],
                },
            ),
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> object:
        """Dispatch a tool call and redact string content in the result."""
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool: {name}"}
        try:
            result = handler(arguments)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        return redact_value(result)

    def _get_test_results(self, arguments: dict[str, Any]) -> object:
        """Summarise tests, optionally failures only."""
        failed_only = bool(arguments.get("failed_only", True))
        rows = []
        for test in self.bundle.tests:
            if not self._test_allowed(test.test_id):
                continue
            if failed_only and test.status not in FAILED and test.status != "flaky":
                if not _recovered(test):
                    continue
            rows.append(_test_summary(test))
        for test in self.bundle.never_ran:
            if not self._test_allowed(test.test_id):
                continue
            rows.append({**_test_summary(test), "never_ran": True})
        return {
            "totals": self.bundle.totals.model_dump(),
            "examined": self.bundle.examined.as_phrase(),
            "tests": rows,
        }

    def _get_diff(self, _arguments: dict[str, Any]) -> object:
        """Return classed file changes."""
        return {
            "changes": [
                {
                    "path": change.path,
                    "kind": change.kind,
                    "added_lines": change.added_lines,
                    "removed_lines": change.removed_lines,
                }
                for change in self.bundle.changes
            ]
        }

    def _get_flow_summary(self, _arguments: dict[str, Any]) -> object:
        """Return API/UI outcomes per flow."""
        flows = [
            flow
            for flow in self.bundle.flows
            if self.allowed_test_ids is None
            or self.allowed_test_ids.intersection(flow.api_test_ids + flow.ui_test_ids)
        ]
        return {
            "flows": [
                {
                    "flow_id": flow.flow_id,
                    "api": flow.api,
                    "ui": flow.ui,
                    "api_test_ids": flow.api_test_ids,
                    "ui_test_ids": flow.ui_test_ids,
                    "ui_http": [
                        {"method": call.method, "url": call.url, "status": call.status_code}
                        for call in flow.ui_http_statuses
                    ],
                }
                for flow in flows
            ]
        }

    def _get_pipeline_steps(self, _arguments: dict[str, Any]) -> object:
        """Return CI step conclusions."""
        return {
            "steps": [
                {
                    "job_name": step.job_name,
                    "name": step.name,
                    "conclusion": step.conclusion,
                    "status": step.status,
                }
                for step in self.bundle.steps
            ]
        }

    def _get_app_logs(self, arguments: dict[str, Any]) -> object:
        """Return a redacted, truncated log excerpt."""
        key = arguments.get("key")
        needle = arguments.get("needle")
        if not isinstance(key, str) or not key:
            key = next(iter(self.bundle.logs), None)
        if key is None or key not in self.bundle.logs:
            return {"error": "no logs", "keys": list(self.bundle.logs)}
        text = redact_text(self.bundle.logs[key])
        excerpt = _excerpt(text, needle if isinstance(needle, str) else None)
        return {"key": key, "excerpt": excerpt, "keys": list(self.bundle.logs)}

    def _read_test_detail(self, arguments: dict[str, Any]) -> object:
        """Return detail for one test id."""
        test_id = arguments.get("test_id")
        if not isinstance(test_id, str):
            return {"error": "test_id required"}
        if not self._test_allowed(test_id):
            return {"error": f"test is outside the classification scope: {test_id}"}
        for test in list(self.bundle.tests) + list(self.bundle.never_ran):
            if test.test_id == test_id:
                return {
                    "test_id": test.test_id,
                    "layer": test.layer,
                    "flow_id": test.flow_id,
                    "status": test.status,
                    "message": redact_text(test.message or "")[:2000],
                    "stack": redact_text(test.stack or "")[:4000],
                    "attempts": [
                        {
                            "attempt": attempt.attempt,
                            "status": attempt.status,
                            "message": redact_text(attempt.message or "")[:500],
                        }
                        for attempt in test.attempts
                    ],
                    "http_calls": [
                        {
                            "method": call.method,
                            "url": call.url,
                            "status_code": call.status_code,
                        }
                        for call in test.http_calls[:20]
                    ],
                    "attachments": test.attachments[:20],
                }
        return {"error": f"unknown test_id: {test_id}"}

    def _get_rule_signals(self, _arguments: dict[str, Any]) -> object:
        """Return the precomputed rules hint string."""
        return {"summary": self.rule_summary or "(none)"}

    def _submit_verdicts(self, arguments: dict[str, Any]) -> object:
        """Accept the model's final verdicts and mark the loop complete."""
        verdicts = arguments.get("verdicts")
        if not isinstance(verdicts, list):
            return {"error": "verdicts must be a list"}
        cleaned: list[dict[str, Any]] = []
        for item in verdicts:
            if not isinstance(item, dict):
                continue
            raw_test_ids = item.get("test_ids")
            if not isinstance(raw_test_ids, list):
                continue
            test_ids = list(
                dict.fromkeys(
                    test_id
                    for test_id in raw_test_ids
                    if isinstance(test_id, str) and self._test_allowed(test_id)
                )
            )
            if test_ids:
                cleaned.append({**item, "test_ids": test_ids})
        self.submitted = cleaned
        return {"accepted": len(cleaned)}

    def _test_allowed(self, test_id: str) -> bool:
        """Return whether a test belongs to the model's classification scope."""
        if self.allowed_test_ids is None:
            return True
        return test_id in self.allowed_test_ids


def clip_tool_result(value: object, *, limit: int = TOOL_RESULT_CHARS) -> str:
    """JSON-encode a tool result and truncate for cost control."""
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return json.dumps(
        {
            "truncated": True,
            "original_chars": len(text),
            "preview": text[: max(0, limit - 100)],
        }
    )


def _test_summary(test: TestResult) -> dict[str, Any]:
    """Compact one test for list views."""
    return {
        "test_id": test.test_id,
        "layer": test.layer,
        "flow_id": test.flow_id,
        "status": test.status,
        "message": redact_text((test.message or "")[:400]),
        "file": test.file,
        "title": test.title,
    }


def _recovered(test: TestResult) -> bool:
    """Return whether a test failed then passed within the same run."""
    if test.status == "flaky":
        return True
    statuses = [attempt.status for attempt in test.attempts]
    return "failed" in statuses and "passed" in statuses


def _excerpt(text: str, needle: str | None) -> str:
    """Keep lines around a needle, or the tail of the log."""
    lines = text.splitlines()
    if not lines:
        return ""
    if needle:
        for index, line in enumerate(lines):
            if needle in line:
                start = max(0, index - LOG_CONTEXT_LINES // 2)
                end = min(len(lines), index + LOG_CONTEXT_LINES // 2 + 1)
                return "\n".join(lines[start:end])
    return "\n".join(lines[-LOG_CONTEXT_LINES:])
