"""Provider-neutral LLM message and tool types."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolSpec(BaseModel):
    """One tool the model may call."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """One function call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    thought_signature: str | None = None


class ChatMessage(BaseModel):
    """One turn in the tool-calling conversation."""

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class TokenUsage(BaseModel):
    """Token counts returned by a provider for one call."""

    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    """Normalized provider response for one generation step."""

    message: ChatMessage
    usage: TokenUsage = Field(default_factory=TokenUsage)
    raw_model: str | None = None
