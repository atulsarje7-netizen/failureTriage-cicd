"""Load repo-specific settings from triage.config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "triage.config.yaml"


class ArtifactPaths(BaseModel):
    """Default locations for CI and local test artifacts."""

    api_junit: str = "test-results/api/junit.xml"
    api_exchanges: str = "test-results/api/exchanges"
    ui_report: str = "tests/ui/test-results/results.json"
    ui_results_dir: str = "tests/ui/test-results"
    app_log: str = "logs/app.log"
    scenario_json: str = "test-results/scenario/scenario.json"
    scenario_diff: str = "test-results/scenario/scenario.diff"


class PathClasses(BaseModel):
    """Path prefixes that class a diff file as product, test, config, or infra."""

    product: list[str] = Field(default_factory=lambda: ["app/"])
    product_ui: list[str] = Field(default_factory=lambda: ["app/web/"])
    test: list[str] = Field(default_factory=lambda: ["tests/"])
    config: list[str] = Field(default_factory=lambda: ["triage.config.yaml", "docker-compose.yml"])
    infra: list[str] = Field(default_factory=lambda: [".github/"])
    dependency: list[str] = Field(
        default_factory=lambda: [
            "pyproject.toml",
            "tests/ui/package.json",
            "tests/ui/package-lock.json",
        ]
    )


class FlowPathRule(BaseModel):
    """Map a test path prefix to a flow id when the report omits one."""

    pattern: str
    flow_id: str


class TriageConfig(BaseModel):
    """Repo-local settings for the collector and the rules classifier."""

    repository: str | None = None
    artifacts: ArtifactPaths = Field(default_factory=ArtifactPaths)
    paths: PathClasses = Field(default_factory=PathClasses)
    flow_path_rules: list[FlowPathRule] = Field(default_factory=list)
    abstain_below: float = 0.75


def load_config(path: Path | None = None) -> TriageConfig:
    """Parse triage.config.yaml into a TriageConfig."""
    config_path = path or DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    raw_ci = raw.get("ci")
    ci: dict[str, Any] = raw_ci if isinstance(raw_ci, dict) else {}
    raw_artifacts = raw.get("artifacts")
    artifacts_raw: dict[str, Any] = raw_artifacts if isinstance(raw_artifacts, dict) else {}
    raw_rules = raw.get("flow_path_rules")
    flow_rules_raw = raw_rules if isinstance(raw_rules, list) else []
    rules = [FlowPathRule.model_validate(item) for item in flow_rules_raw if isinstance(item, dict)]
    repository = ci.get("repository") if isinstance(ci.get("repository"), str) else None
    artifacts = ArtifactPaths.model_validate(artifacts_raw) if artifacts_raw else ArtifactPaths()
    raw_paths = raw.get("paths")
    paths = PathClasses.model_validate(raw_paths) if isinstance(raw_paths, dict) else PathClasses()
    raw_thresholds = raw.get("thresholds")
    abstain_below = 0.75
    if isinstance(raw_thresholds, dict):
        candidate = raw_thresholds.get("abstain_below")
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            abstain_below = float(candidate)
    return TriageConfig(
        repository=repository,
        artifacts=artifacts,
        paths=paths,
        flow_path_rules=rules,
        abstain_below=abstain_below,
    )


def resolve_flow_id(test_id: str, file_path: str | None, rules: list[FlowPathRule]) -> str | None:
    """Return the first flow_id whose pattern matches the test id or file path."""
    haystacks = [test_id]
    if file_path:
        haystacks.append(file_path)
    for rule in rules:
        for haystack in haystacks:
            if rule.pattern in haystack:
                return rule.flow_id
    return None
