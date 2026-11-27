"""Load local .env values into the process environment without overwriting."""

from __future__ import annotations

import os
from pathlib import Path

from triage.config import ROOT

DEFAULT_ENV_PATH = ROOT / ".env"


def load_dotenv(path: Path | None = None) -> Path | None:
    """Read KEY=VALUE lines from .env into os.environ when the key is unset."""
    env_path = path or DEFAULT_ENV_PATH
    if not env_path.is_file():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'").strip('"')
    return env_path
