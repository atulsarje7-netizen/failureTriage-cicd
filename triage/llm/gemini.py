"""Gemini generateContent client behind the provider-neutral interface."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx

from triage.llm.redact import redact_text
from triage.llm.types import (
    ChatMessage,
    LLMResponse,
    TokenUsage,
    ToolCall,
    ToolSpec,
)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiClient:
    """Call Gemini's generateContent API with function declarations."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        base_url: str = DEFAULT_BASE,
        timeout_s: float = 60.0,
    ) -> None:
        """Configure the Gemini endpoint from args or environment."""
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Put it in a local .env (gitignored) or the environment."
            )
        self._api_key = key
        self._model = model or os.environ.get("TRIAGE_LLM_MODEL") or DEFAULT_MODEL
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    @property
    def model_name(self) -> str:
        """Return the configured model id."""
        return self._model

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
    ) -> LLMResponse:
        """POST one generateContent turn and normalize the reply."""
        system, contents = _to_gemini_contents(messages)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [_decl(tool) for tool in tools]}]
        url = f"{self._base_url}/models/{self._model}:generateContent"
        headers = {"x-goog-api-key": self._api_key}
        with httpx.Client(timeout=self._timeout_s) as client:
            response = client.post(url, headers=headers, json=body)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Keep the key out of exception text (query-string auth used to leak it).
                detail = redact_text(response.text.replace("\n", " ")[:240])
                raise httpx.HTTPStatusError(
                    f"Gemini HTTP {response.status_code} for model {self._model}: {detail}",
                    request=exc.request,
                    response=exc.response,
                ) from None
            payload = response.json()
        return _from_gemini(payload, self._model)


def _decl(tool: ToolSpec) -> dict[str, Any]:
    """Convert a ToolSpec into a Gemini functionDeclaration."""
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters
        or {
            "type": "object",
            "properties": {},
        },
    }


def _to_gemini_contents(messages: list[ChatMessage]) -> tuple[str | None, list[dict[str, Any]]]:
    """Split system text and map chat turns into Gemini contents."""
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    pending_tool_parts: list[dict[str, Any]] = []

    def flush_tools() -> None:
        """Emit batched functionResponse parts as one user turn."""
        nonlocal pending_tool_parts
        if pending_tool_parts:
            contents.append({"role": "user", "parts": pending_tool_parts})
            pending_tool_parts = []

    for message in messages:
        if message.role == "system":
            if message.content:
                system_parts.append(message.content)
            continue
        if message.role == "user":
            flush_tools()
            contents.append({"role": "user", "parts": [{"text": message.content or ""}]})
            continue
        if message.role == "assistant":
            flush_tools()
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"text": message.content})
            for call in message.tool_calls:
                function_call: dict[str, Any] = {
                    "name": call.name,
                    "args": call.arguments,
                }
                if call.id:
                    function_call["id"] = call.id
                part: dict[str, Any] = {"functionCall": function_call}
                # Gemini 3.x requires echoing thought signatures on later turns.
                if call.thought_signature:
                    part["thoughtSignature"] = call.thought_signature
                parts.append(part)
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue
        if message.role == "tool":
            response_body: dict[str, Any] = {
                "name": message.name or "tool",
                "response": _tool_response_payload(message.content),
            }
            if message.tool_call_id:
                response_body["id"] = message.tool_call_id
            pending_tool_parts.append({"functionResponse": response_body})
    flush_tools()
    system = "\n\n".join(system_parts) if system_parts else None
    return system, contents


def _tool_response_payload(content: str | None) -> dict[str, Any]:
    """Wrap tool output as a Gemini functionResponse object."""
    if not content:
        return {"result": ""}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"result": content}
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


def _from_gemini(payload: dict[str, Any], model: str) -> LLMResponse:
    """Parse a generateContent response into ChatMessage + usage."""
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {payload!r}")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text_bits: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if "text" in part and isinstance(part["text"], str):
            text_bits.append(part["text"])
        call = part.get("functionCall")
        if isinstance(call, dict) and isinstance(call.get("name"), str):
            args = call.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            call_id = call.get("id")
            signature = part.get("thoughtSignature")
            tool_calls.append(
                ToolCall(
                    id=str(call_id) if call_id else str(uuid.uuid4()),
                    name=call["name"],
                    arguments=args,
                    thought_signature=signature if isinstance(signature, str) else None,
                )
            )
    usage_meta = payload.get("usageMetadata") or {}
    usage = TokenUsage(
        input_tokens=int(usage_meta.get("promptTokenCount") or 0),
        output_tokens=int(usage_meta.get("candidatesTokenCount") or 0),
    )
    return LLMResponse(
        message=ChatMessage(
            role="assistant",
            content="\n".join(text_bits) if text_bits else None,
            tool_calls=tool_calls,
        ),
        usage=usage,
        raw_model=model,
    )
