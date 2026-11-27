"""Unit tests for redaction, evidence tools, and the capped LLM loop."""

from __future__ import annotations

from triage.classify.recording import Tracer
from triage.collect.assemble import build_bundle
from triage.config import TriageConfig
from triage.llm.classify import run_llm
from triage.llm.gemini import GeminiClient
from triage.llm.loop import run_tool_loop
from triage.llm.redact import REDACTED, redact_text, redact_value
from triage.llm.tools import EvidenceToolkit
from triage.llm.types import ChatMessage, LLMResponse, TokenUsage, ToolCall, ToolSpec
from triage.schemas.bundle import EvidenceBundle, FileChange, HttpCall, RunMetadata
from triage.schemas.bundle import TestResult as Result


class ScriptedClient:
    """Return a fixed sequence of LLM responses for tests."""

    def __init__(self, responses: list[LLMResponse], model: str = "test-model") -> None:
        """Store scripted replies."""
        self._responses = list(responses)
        self._model = model
        self.calls = 0

    @property
    def model_name(self) -> str:
        """Return the fake model id."""
        return self._model

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
    ) -> LLMResponse:
        """Pop the next scripted response."""
        del messages, tools
        if not self._responses:
            raise RuntimeError("no scripted responses left")
        self.calls += 1
        return self._responses.pop(0)


def _bundle() -> EvidenceBundle:
    """Build a tiny failed API bundle for LLM tests."""
    return build_bundle(
        run=RunMetadata(run_id="t1", source="local"),
        tests=[
            Result(
                test_id="tests/api/test_orders.py::test_checkout",
                layer="api",
                flow_id="checkout",
                status="failed",
                message="assert 500 == 201",
                file="tests/api/test_orders.py",
            ),
            Result(
                test_id="tests/ui/checkout.spec.ts > places order",
                layer="ui",
                flow_id="checkout",
                status="passed",
                file="tests/ui/checkout.spec.ts",
            ),
        ],
        changes=[FileChange(path="app/api/orders.py", kind="product", added_lines=2)],
        logs={"app": "password=super-secret POST /checkout 500"},
    )


def test_redact_strips_password_and_gemini_shaped_keys() -> None:
    """Common secret shapes are replaced before model input."""
    text = redact_text("password=super-secret and AQ." + ("x" * 40))
    assert "super-secret" not in text
    assert "AQ.xxxx" not in text
    assert REDACTED in text


def test_redact_recurses_through_structured_tool_results() -> None:
    """Secrets are removed from nested values and fields named like credentials."""
    result = redact_value(
        {
            "attempts": [{"message": "token=abc123"}],
            "api_key": "plain-secret-value",
            "http": [{"url": "https://example.test/?password=hidden"}],
        }
    )
    assert "abc123" not in str(result)
    assert "plain-secret-value" not in str(result)
    assert "hidden" not in str(result)


def test_toolkit_redacts_logs_and_accepts_submit() -> None:
    """Log tools redact secrets and submit_verdicts stores the payload."""
    toolkit = EvidenceToolkit(_bundle())
    logs = toolkit.call("get_app_logs", {"key": "app"})
    assert isinstance(logs, dict)
    assert "super-secret" not in str(logs)
    result = toolkit.call(
        "submit_verdicts",
        {
            "verdicts": [
                {
                    "test_ids": ["tests/api/test_orders.py::test_checkout"],
                    "label": "product_bug",
                    "confidence": 0.9,
                    "evidence": [
                        {
                            "source": "diff",
                            "ref": "app/api/orders.py",
                            "note": "product change",
                        }
                    ],
                }
            ]
        },
    )
    assert result == {"accepted": 1}
    assert toolkit.submitted is not None
    assert toolkit.submitted[0]["label"] == "product_bug"


def test_tool_loop_hits_cap_and_abstains() -> None:
    """Past the tool-call budget the loop stops without a final submit."""
    toolkit = EvidenceToolkit(_bundle())
    responses = [
        LLMResponse(
            message=ChatMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(id="1", name="get_diff", arguments={}),
                ],
            ),
            usage=TokenUsage(input_tokens=10, output_tokens=2),
        ),
        LLMResponse(
            message=ChatMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(id="2", name="get_test_results", arguments={}),
                ],
            ),
            usage=TokenUsage(input_tokens=10, output_tokens=2),
        ),
        LLMResponse(
            message=ChatMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(id="3", name="get_app_logs", arguments={}),
                ],
            ),
            usage=TokenUsage(input_tokens=10, output_tokens=2),
        ),
    ]
    client = ScriptedClient(responses)
    tracer = Tracer()
    result = run_tool_loop(
        client,
        toolkit,
        [ChatMessage(role="user", content="classify")],
        max_tool_calls=2,
        tracer=tracer,
    )
    assert result.capped is True
    assert result.verdicts is None
    assert result.tool_calls == 2
    assert any(event.kind == "tool" for event in tracer.events)
    assert any(event.kind == "llm" for event in tracer.events)


def test_run_llm_keeps_decisive_rules_without_model() -> None:
    """Clear rule verdicts skip the model and still return a report."""
    bundle = _bundle()
    client = ScriptedClient([])
    report, trace = run_llm(bundle, TriageConfig(max_tool_calls=4), client)
    assert client.calls == 0
    assert report.verdicts
    assert all(verdict.label == "product_bug" for verdict in report.verdicts)
    assert any(event.name == "llm_skipped" for event in trace.events)


def test_run_llm_uses_submit_when_rules_abstain() -> None:
    """Ambiguous rule cases call the model and accept checked citations."""
    bundle = build_bundle(
        run=RunMetadata(run_id="amb", source="local"),
        tests=[
            Result(
                test_id="tests/ui/checkout.spec.ts > places order",
                layer="ui",
                flow_id="checkout",
                status="failed",
                message="Timeout 5000ms exceeded",
                file="tests/ui/checkout.spec.ts",
            )
        ],
        changes=[],
        logs={},
    )
    client = ScriptedClient(
        [
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id="s1",
                            name="submit_verdicts",
                            arguments={
                                "verdicts": [
                                    {
                                        "test_ids": ["tests/ui/checkout.spec.ts > places order"],
                                        "label": "needs_human",
                                        "confidence": 0.4,
                                        "tags": ["ui", "timeout"],
                                        "evidence": [
                                            {
                                                "source": "test",
                                                "ref": ("tests/ui/checkout.spec.ts > places order"),
                                                "note": "timeout without layer pair",
                                            }
                                        ],
                                    }
                                ]
                            },
                        )
                    ],
                ),
                usage=TokenUsage(input_tokens=20, output_tokens=15),
            )
        ]
    )
    report, trace = run_llm(bundle, TriageConfig(max_tool_calls=8), client)
    assert client.calls == 1
    assert report.verdicts
    assert report.verdicts[0].label == "needs_human"
    assert report.verdicts[0].versions["model"] == "test-model"
    assert any(event.kind == "llm" for event in trace.events)


def test_run_llm_keeps_rule_decisions_and_limits_model_to_ambiguous_tests() -> None:
    """The model cannot duplicate or replace a decision already made by rules."""
    clear_id = "tests/api/test_orders.py::test_checkout"
    ambiguous_id = "tests/ui/login.spec.ts > login"
    bundle = build_bundle(
        run=RunMetadata(run_id="mixed", source="local"),
        tests=[
            Result(
                test_id=clear_id,
                layer="api",
                flow_id="checkout",
                status="failed",
                message="assert 500 == 201",
            ),
            Result(
                test_id="tests/api/test_auth.py::test_login",
                layer="api",
                flow_id="login",
                status="passed",
            ),
            Result(
                test_id=ambiguous_id,
                layer="ui",
                flow_id="login",
                status="failed",
                message="HTTP 500",
                http_calls=[
                    HttpCall(
                        method="POST",
                        url="/auth/login",
                        status_code=500,
                        source="playwright_trace",
                    )
                ],
            ),
        ],
        changes=[FileChange(path="app/api/orders.py", kind="product")],
    )
    client = ScriptedClient(
        [
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id="s1",
                            name="submit_verdicts",
                            arguments={
                                "verdicts": [
                                    {
                                        "test_ids": [clear_id],
                                        "label": "test_bug",
                                        "confidence": 0.99,
                                        "evidence": [],
                                    },
                                    {
                                        "test_ids": [ambiguous_id],
                                        "label": "environment_issue",
                                        "confidence": 0.86,
                                        "evidence": [
                                            {
                                                "source": "test",
                                                "ref": ambiguous_id,
                                                "note": "HTTP 500",
                                            }
                                        ],
                                    },
                                ]
                            },
                        )
                    ],
                )
            )
        ]
    )

    report, _trace = run_llm(bundle, TriageConfig(), client)

    by_id = {verdict.test_id: verdict for verdict in report.verdicts}
    assert set(by_id) == {clear_id, ambiguous_id}
    assert by_id[clear_id].label == "product_bug"
    assert by_id[ambiguous_id].label == "environment_issue"


def test_run_llm_skips_provider_for_bundle_without_failures() -> None:
    """A clean run returns no verdicts without making a provider call."""
    bundle = build_bundle(run=RunMetadata(run_id="green", source="local"), tests=[])
    client = ScriptedClient([])

    report, trace = run_llm(bundle, TriageConfig(), client)

    assert report.verdicts == []
    assert client.calls == 0
    assert any(event.name == "llm_skipped" for event in trace.events)


def test_gemini_client_sends_tool_messages_with_header_auth(monkeypatch) -> None:
    """The Gemini adapter uses header auth and preserves tool-call metadata."""
    requests: list[dict[str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "get_diff",
                                        "args": {},
                                        "id": "call-1",
                                    },
                                    "thoughtSignature": "signature-1",
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 3},
            }

    class Client:
        def __init__(self, *, timeout: float) -> None:
            requests.append({"timeout": timeout})

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
            requests.append({"url": url, "headers": headers, "json": json})
            return Response()

    monkeypatch.setattr("triage.llm.gemini.httpx.Client", Client)
    client = GeminiClient(api_key="test-secret", model="test-model")

    result = client.complete(
        [
            ChatMessage(role="system", content="system"),
            ChatMessage(role="user", content="inspect"),
        ],
        [ToolSpec(name="get_diff", description="Read diff")],
    )

    request = requests[1]
    assert request["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
    )
    assert request["headers"] == {"x-goog-api-key": "test-secret"}
    assert "test-secret" not in str(request["url"])
    assert result.message.tool_calls[0].thought_signature == "signature-1"
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 3
