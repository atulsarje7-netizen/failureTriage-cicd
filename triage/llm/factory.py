"""Build an LLM client from config and environment."""

from __future__ import annotations

import os

from triage.config import TriageConfig
from triage.env import load_dotenv
from triage.llm.base import LLMClient
from triage.llm.gemini import GeminiClient


def build_llm_client(config: TriageConfig | None = None) -> LLMClient:
    """Return the configured provider client (Gemini today)."""
    load_dotenv()
    cfg = config
    provider = (cfg.llm_provider if cfg else None) or os.environ.get(
        "TRIAGE_LLM_PROVIDER", "gemini"
    )
    model = (cfg.llm_model if cfg else None) or os.environ.get("TRIAGE_LLM_MODEL")
    if provider.lower() != "gemini":
        raise ValueError(f"unsupported LLM provider: {provider}")
    return GeminiClient(model=model)
