"""Provider-neutral LLM client protocol."""

from __future__ import annotations

from typing import Protocol

from triage.llm.types import ChatMessage, LLMResponse, ToolSpec


class LLMClient(Protocol):
    """Minimal chat interface used by the tool loop."""

    @property
    def model_name(self) -> str:
        """Return the provider model id stamped on verdicts."""

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
    ) -> LLMResponse:
        """Generate the next assistant turn, optionally requesting tool calls."""
