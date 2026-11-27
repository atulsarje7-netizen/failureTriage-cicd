"""Collect an evidence bundle from local artifact directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from triage.collect.assemble import (
    attach_api_exchanges,
    attach_ui_traces,
    build_bundle,
    save_bundle,
)
from triage.collect.diff import parse_unified_diff
from triage.collect.junit import JUnitParser
from triage.collect.playwright_json import PlaywrightJsonParser
from triage.config import TriageConfig, load_config
from triage.schemas.bundle import EvidenceBundle, FileChange, PipelineStep, RunMetadata, TestResult


def collect_local(
    root: Path | None = None,
    *,
    config: TriageConfig | None = None,
    run_id: str | None = None,
    out_path: Path | None = None,
) -> EvidenceBundle:
    """Build a bundle from local JUnit, Playwright, exchange, and log files."""
    cfg = config or load_config()
    base = root or Path.cwd()
    artifacts = cfg.artifacts
    notes: list[str] = []
    artifact_paths: dict[str, str] = {}
    logs: dict[str, str] = {}

    junit_path = base / artifacts.api_junit
    ui_report = base / artifacts.ui_report
    exchange_dir = base / artifacts.api_exchanges
    ui_results_dir = base / artifacts.ui_results_dir
    app_log = base / artifacts.app_log
    scenario_path = base / artifacts.scenario_json

    tests: list[TestResult] = []
    if junit_path.is_file():
        tests.extend(JUnitParser(cfg.flow_path_rules).parse(junit_path))
        artifact_paths["api_junit"] = str(junit_path)
    else:
        notes.append(f"missing API JUnit report: {junit_path}")

    if ui_report.is_file():
        tests.extend(PlaywrightJsonParser(cfg.flow_path_rules).parse(ui_report))
        artifact_paths["ui_report"] = str(ui_report)
    else:
        notes.append(f"missing Playwright JSON report: {ui_report}")

    attach_api_exchanges(tests, exchange_dir if exchange_dir.is_dir() else None)
    if exchange_dir.is_dir():
        artifact_paths["api_exchanges"] = str(exchange_dir)
    attach_ui_traces(tests, ui_results_dir if ui_results_dir.is_dir() else None)
    if ui_results_dir.is_dir():
        artifact_paths["ui_results_dir"] = str(ui_results_dir)

    if app_log.is_file():
        logs["app"] = _read_log(app_log)
        artifact_paths["app_log"] = str(app_log)

    scenario = _load_json(scenario_path)
    if scenario:
        artifact_paths["scenario_json"] = str(scenario_path)

    changes = _load_changes(base / artifacts.scenario_diff, cfg, artifact_paths)
    _load_sidecar_logs(base, artifacts.app_log, logs, artifact_paths)
    steps = _pretest_steps(logs)

    run = RunMetadata(
        run_id=run_id or (str(scenario.get("id")) if scenario else "local"),
        workflow="local",
        conclusion=_local_conclusion(tests),
        status="completed",
        repository=cfg.repository,
        source="local",
    )
    if scenario:
        notes.append(f"scenario {scenario.get('id')} expected label={scenario.get('label')}")

    bundle = build_bundle(
        run=run,
        tests=tests,
        steps=steps,
        artifact_paths=artifact_paths,
        logs=logs,
        notes=notes,
        changes=changes,
    )
    if out_path is not None:
        save_bundle(bundle, out_path)
    return bundle


def collect_from_artifact_tree(
    artifact_root: Path,
    *,
    config: TriageConfig | None = None,
    run: RunMetadata | None = None,
    steps: list[PipelineStep] | None = None,
    logs: dict[str, str] | None = None,
    out_path: Path | None = None,
) -> EvidenceBundle:
    """Build a bundle from a downloaded GitHub artifact tree."""
    cfg = config or load_config()
    notes: list[str] = []
    artifact_paths: dict[str, str] = {"artifact_root": str(artifact_root)}
    tests: list[TestResult] = []

    junit = _find_named(artifact_root, "junit.xml")
    ui_report = _find_named(artifact_root, "results.json")
    exchange_dir = _find_dir(artifact_root, "exchanges")
    ui_results_dir = ui_report.parent if ui_report else _find_dir(artifact_root, "test-results")

    if junit:
        tests.extend(JUnitParser(cfg.flow_path_rules).parse(junit))
        artifact_paths["api_junit"] = str(junit)
    else:
        notes.append("no junit.xml found in artifacts")

    if ui_report:
        tests.extend(PlaywrightJsonParser(cfg.flow_path_rules).parse(ui_report))
        artifact_paths["ui_report"] = str(ui_report)
    else:
        notes.append("no Playwright results.json found in artifacts")

    attach_api_exchanges(tests, exchange_dir)
    if exchange_dir:
        artifact_paths["api_exchanges"] = str(exchange_dir)
    attach_ui_traces(tests, ui_results_dir)
    if ui_results_dir:
        artifact_paths["ui_results_dir"] = str(ui_results_dir)

    merged_logs = dict(logs or {})
    for log_path in artifact_root.rglob("*.log"):
        key = log_path.stem
        merged_logs[key] = _read_log(log_path)
        artifact_paths[f"log:{key}"] = str(log_path)

    never_ran = _never_ran_from_failed_steps(steps or [], tests)
    changes = _load_tree_diff(artifact_root, cfg, artifact_paths)
    bundle = build_bundle(
        run=run
        or RunMetadata(
            run_id="artifacts",
            source="local",
            repository=cfg.repository,
        ),
        tests=tests,
        steps=steps,
        never_ran=never_ran,
        artifact_paths=artifact_paths,
        logs=merged_logs,
        notes=notes,
        changes=changes,
    )
    if out_path is not None:
        save_bundle(bundle, out_path)
    return bundle


def _never_ran_from_failed_steps(
    steps: list[PipelineStep],
    tests: list[TestResult],
) -> list[TestResult]:
    """When a pre-test step failed and no tests exist, record never_ran."""
    if tests:
        return []
    for step in steps:
        name = step.name.lower()
        if step.conclusion in {"failure", "cancelled"} and any(
            token in name for token in ("install", "lint", "build", "setup")
        ):
            return [
                TestResult(
                    test_id="(suite)",
                    layer="api",
                    status="never_ran",
                    message=f"step '{step.name}' failed before tests ran",
                )
            ]
    return []


def _load_changes(
    diff_path: Path,
    config: TriageConfig,
    artifact_paths: dict[str, str],
) -> list[FileChange]:
    """Parse a unified diff file into classed file changes when it exists."""
    if not diff_path.is_file():
        return []
    artifact_paths["scenario_diff"] = str(diff_path)
    return parse_unified_diff(diff_path.read_text(encoding="utf-8", errors="replace"), config.paths)


def _load_tree_diff(
    artifact_root: Path,
    config: TriageConfig,
    artifact_paths: dict[str, str],
) -> list[FileChange]:
    """Find scenario.diff under a downloaded artifact tree and class its files."""
    diff_path = _find_named(artifact_root, "scenario.diff")
    if diff_path is None:
        return []
    return _load_changes(diff_path, config, artifact_paths)


def _load_sidecar_logs(
    base: Path,
    app_log: str,
    logs: dict[str, str],
    artifact_paths: dict[str, str],
) -> None:
    """Read install, lint, build, setup, and compose logs that sit beside the app log."""
    log_dir = (base / app_log).parent
    for name in ("install", "lint", "build", "setup", "compose"):
        path = log_dir / f"{name}.log"
        if path.is_file():
            logs[name] = _read_log(path)
            artifact_paths[f"log:{name}"] = str(path)


def _pretest_steps(logs: dict[str, str]) -> list[PipelineStep]:
    """Record install, lint, and build steps from sidecar logs when present."""
    steps: list[PipelineStep] = []
    mapping = (
        ("install", "Install dependencies"),
        ("lint", "Lint"),
        ("build", "Build"),
    )
    for key, name in mapping:
        text = logs.get(key)
        if not text:
            continue
        steps.append(
            PipelineStep(
                job_name="local",
                name=name,
                conclusion="failure" if _sidecar_failed(key, text) else "success",
                log_excerpt=text[:4000],
            )
        )
    return steps


def _sidecar_failed(stage: str, text: str) -> bool:
    """Return whether a local sidecar log shows a failed pre-test stage."""
    lowered = text.lower()
    if stage == "install":
        return (
            any(
                token in text
                for token in (
                    "No matching distribution",
                    "Could not find a version",
                    "Connection refused",
                    "Could not resolve host",
                )
            )
            or "error:" in lowered
        )
    if stage == "lint":
        return "error" in lowered or "found " in lowered
    if stage == "build":
        return any(
            token in lowered
            for token in ("error", "failed to solve", "failed to build", "manifest unknown")
        )
    return False


def _local_conclusion(tests: list[TestResult]) -> str:
    """Infer a local run conclusion from test statuses."""
    if any(t.status in {"failed", "timed_out", "interrupted"} for t in tests):
        return "failure"
    if tests:
        return "success"
    return "unknown"


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON object from disk when the file exists."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_log(path: Path, max_chars: int = 100_000) -> str:
    """Read a log file with a soft size cap."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated]...\n"
    return text


def _find_named(root: Path, name: str) -> Path | None:
    """Return the first file with this name under root."""
    matches = sorted(path for path in root.rglob(name) if path.is_file())
    return matches[0] if matches else None


def _find_dir(root: Path, name: str) -> Path | None:
    """Return the first directory with the given name under root."""
    if (root / name).is_dir():
        return root / name
    matches = [path for path in root.rglob(name) if path.is_dir()]
    return sorted(matches)[0] if matches else None
