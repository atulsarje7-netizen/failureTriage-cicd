"""Load repo-specific settings from triage.config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from triage.budget import BudgetSettings

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
    infra: list[str] = Field(default_factory=lambda: [".github/", "Dockerfile"])
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


class VerifySettings(BaseModel):
    """Allow-listed same-SHA and last-green rerun settings."""

    enabled: bool = False
    same_sha_attempts: int = Field(default=3, ge=1, le=10)
    last_green: bool = True
    repo_root: str = "."
    ui_cwd: str = "tests/ui"
    timeout_seconds: float = Field(default=120.0, gt=0.0)
    last_green_sha: str | None = None


class ConfidenceSettings(BaseModel):
    """Weights for combining rule agreement, reruns, and sample agreement."""

    weight_rule: float = Field(default=0.4, ge=0.0, le=1.0)
    weight_rerun: float = Field(default=0.4, ge=0.0, le=1.0)
    weight_sample: float = Field(default=0.2, ge=0.0, le=1.0)
    llm_samples: int = Field(default=1, ge=1, le=5)


class PublishSettings(BaseModel):
    """Where and how draft bugs are posted after triage."""

    pr_comment: bool = True
    draft_issue: bool = True
    draft_issue_label: str = "triage:draft"


class HistorySettings(BaseModel):
    """SQLite history store path and quarantine recommendation threshold."""

    path: str = ".triage/history.db"
    quarantine_recommend_at: float = Field(default=0.3, ge=0.0, le=1.0)
    enabled: bool = True


class BudgetSettingsModel(BaseModel):
    """Per-run cost and latency ceilings; null means unlimited for that dimension."""

    max_latency_ms: float | None = Field(default=120_000.0, gt=0.0)
    max_tokens: int | None = Field(default=200_000, ge=1)
    max_cost_usd: float | None = Field(default=0.50, gt=0.0)
    input_cost_per_1m: float = Field(default=0.10, ge=0.0)
    output_cost_per_1m: float = Field(default=0.40, ge=0.0)


class AuditSettings(BaseModel):
    """Append-only audit log path for verdicts and publishes."""

    path: str = ".triage/audit.jsonl"
    enabled: bool = True


class FeedbackSettings(BaseModel):
    """Where human confirm/correct feedback is stored for Evals."""

    path: str = ".triage/feedback.jsonl"


class TriageConfig(BaseModel):
    """Repo-local settings for the collector and classifiers."""

    repository: str | None = None
    artifacts: ArtifactPaths = Field(default_factory=ArtifactPaths)
    paths: PathClasses = Field(default_factory=PathClasses)
    flow_path_rules: list[FlowPathRule] = Field(default_factory=list)
    abstain_below: float = Field(default=0.75, ge=0.0, le=1.0)
    max_tool_calls: int = Field(default=12, ge=1, le=100)
    llm_provider: str = "gemini"
    llm_model: str | None = None
    verify: VerifySettings = Field(default_factory=VerifySettings)
    confidence: ConfidenceSettings = Field(default_factory=ConfidenceSettings)
    publish: PublishSettings = Field(default_factory=PublishSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)
    budgets: BudgetSettingsModel = Field(default_factory=BudgetSettingsModel)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    feedback: FeedbackSettings = Field(default_factory=FeedbackSettings)


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
    max_tool_calls = 12
    if isinstance(raw_thresholds, dict):
        candidate = raw_thresholds.get("abstain_below")
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            abstain_below = float(candidate)
        tool_cap = raw_thresholds.get("max_tool_calls")
        if isinstance(tool_cap, int) and not isinstance(tool_cap, bool):
            max_tool_calls = tool_cap
    raw_llm = raw.get("llm")
    llm_provider = "gemini"
    llm_model: str | None = None
    if isinstance(raw_llm, dict):
        if isinstance(raw_llm.get("provider"), str):
            llm_provider = raw_llm["provider"]
        if isinstance(raw_llm.get("model"), str):
            llm_model = raw_llm["model"]
    raw_verify = raw.get("verify")
    verify = (
        VerifySettings.model_validate(raw_verify)
        if isinstance(raw_verify, dict)
        else VerifySettings()
    )
    raw_confidence = raw.get("confidence")
    confidence = (
        ConfidenceSettings.model_validate(raw_confidence)
        if isinstance(raw_confidence, dict)
        else ConfidenceSettings()
    )
    raw_publish = raw.get("publish")
    publish = (
        PublishSettings.model_validate(raw_publish)
        if isinstance(raw_publish, dict)
        else PublishSettings()
    )
    raw_history = raw.get("history")
    history = (
        HistorySettings.model_validate(raw_history)
        if isinstance(raw_history, dict)
        else HistorySettings()
    )
    raw_budgets = raw.get("budgets")
    budgets = (
        BudgetSettingsModel.model_validate(raw_budgets)
        if isinstance(raw_budgets, dict)
        else BudgetSettingsModel()
    )
    raw_audit = raw.get("audit")
    audit = (
        AuditSettings.model_validate(raw_audit) if isinstance(raw_audit, dict) else AuditSettings()
    )
    raw_feedback = raw.get("feedback")
    feedback = (
        FeedbackSettings.model_validate(raw_feedback)
        if isinstance(raw_feedback, dict)
        else FeedbackSettings()
    )
    return TriageConfig(
        repository=repository,
        artifacts=artifacts,
        paths=paths,
        flow_path_rules=rules,
        abstain_below=abstain_below,
        max_tool_calls=max_tool_calls,
        llm_provider=llm_provider,
        llm_model=llm_model,
        verify=verify,
        confidence=confidence,
        publish=publish,
        history=history,
        budgets=budgets,
        audit=audit,
        feedback=feedback,
    )


def budget_settings_from_config(config: TriageConfig) -> BudgetSettings:
    """Convert config budget fields into a BudgetGuard settings object."""
    from triage.budget import BudgetSettings as Settings

    raw = config.budgets
    return Settings(
        max_latency_ms=raw.max_latency_ms,
        max_tokens=raw.max_tokens,
        max_cost_usd=raw.max_cost_usd,
        input_cost_per_1m=raw.input_cost_per_1m,
        output_cost_per_1m=raw.output_cost_per_1m,
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
