from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "faults" / "manifest.yaml"
INSTALL_PACKAGE = "failure-triage-missing-dependency==0.0.0"

LABELS = frozenset({"product_bug", "test_bug", "flaky_test", "environment_issue"})
CAUSE_TAGS = frozenset(
    {
        "timeout",
        "selector-not-found",
        "assertion-mismatch",
        "http-5xx",
        "http-4xx",
        "auth",
        "test-data",
        "race-condition",
        "order-dependent",
        "contract-change",
        "dependency-install",
        "service-unavailable",
        "network",
        "config",
        "runner-resource",
    }
)
LAYER_TAGS = frozenset({"api", "ui", "cross-layer"})
TAGS = CAUSE_TAGS | LAYER_TAGS
SERVICES = frozenset({"db", "pay"})
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Expectation:
    """Test names a scenario must fail and names it must still pass."""

    api_failed: tuple[str, ...]
    api_passed: tuple[str, ...]
    ui_failed: tuple[str, ...]
    ui_passed: tuple[str, ...]


@dataclass(frozen=True)
class Scenario:
    """One injected fault and the label it is supposed to produce."""

    id: str
    label: str
    tags: tuple[str, ...]
    flow_id: str
    stage: str
    summary: str
    patch: str | None
    services: tuple[str, ...]
    env: dict[str, str]
    whole_run: bool
    run_api: bool
    api_nodes: tuple[str, ...]
    run_ui: bool
    ui_grep: str | None
    expect: Expectation
    passes_with_env: dict[str, str]

    def to_public_dict(self) -> dict[str, object]:
        """Return the label metadata stored with a scenario run."""
        return {
            "id": self.id,
            "label": self.label,
            "tags": list(self.tags),
            "flow_id": self.flow_id,
            "stage": self.stage,
            "summary": self.summary,
            "whole_run": self.whole_run,
            "passes_with_env": self.passes_with_env,
        }


def load_scenarios(path: Path | None = None) -> tuple[Scenario, ...]:
    """Load and validate every scenario in the manifest."""
    manifest = path or MANIFEST_PATH
    raw = yaml.safe_load(manifest.read_text())
    if not isinstance(raw, dict):
        raise ValueError("manifest root must be a mapping")
    if raw.get("version") != 1:
        raise ValueError("manifest version must be 1")
    entries = raw.get("scenarios")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest scenarios must be a non-empty list")
    scenarios = tuple(_scenario(entry, manifest.parent) for entry in entries)
    ids = [item.id for item in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario ids must be unique")
    return scenarios


def get_scenario(scenario_id: str, path: Path | None = None) -> Scenario:
    """Return one scenario by id."""
    if not _ID.fullmatch(scenario_id):
        raise ValueError(f"invalid scenario id: {scenario_id}")
    scenarios = load_scenarios(path)
    for scenario in scenarios:
        if scenario.id == scenario_id:
            return scenario
    known = ", ".join(item.id for item in scenarios)
    raise ValueError(f"unknown scenario {scenario_id}. Known ids: {known}")


def _scenario(entry: object, base: Path) -> Scenario:
    """Parse one manifest entry."""
    data = _mapping(entry, "scenario")
    allowed = {
        "id",
        "label",
        "tags",
        "flow_id",
        "stage",
        "summary",
        "patch",
        "services",
        "env",
        "whole_run",
        "run",
        "expect",
        "passes_with_env",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown scenario fields: {sorted(unknown)}")
    scenario_id = _text(data, "id")
    if not _ID.fullmatch(scenario_id):
        raise ValueError(f"invalid scenario id: {scenario_id}")
    label = _text(data, "label")
    if label not in LABELS:
        raise ValueError(f"{scenario_id}: unknown label {label}")
    tags = _str_tuple(data.get("tags"), f"{scenario_id} tags")
    bad_tags = [tag for tag in tags if tag not in TAGS]
    if bad_tags:
        raise ValueError(f"{scenario_id}: unknown tags {bad_tags}")
    stage = _text(data, "stage")
    if stage not in {"test", "install"}:
        raise ValueError(f"{scenario_id}: stage must be test or install")
    if stage == "test" and not LAYER_TAGS.intersection(tags):
        raise ValueError(f"{scenario_id}: test scenarios need a layer tag")
    if "cross-layer" in tags and not {"api", "ui"} <= set(tags):
        raise ValueError(f"{scenario_id}: cross-layer needs api and ui tags")
    if {"api", "ui"} <= set(tags) and "cross-layer" not in tags:
        raise ValueError(f"{scenario_id}: api and ui tags need cross-layer")
    services = _str_tuple(data.get("services", []), f"{scenario_id} services")
    if any(service not in SERVICES for service in services):
        raise ValueError(f"{scenario_id}: services must be db or pay")
    patch = data.get("patch")
    patch_path: str | None
    if patch is None:
        patch_path = None
    else:
        if not isinstance(patch, str):
            raise ValueError(f"{scenario_id}: patch must be a string")
        candidate = base / patch
        if not candidate.is_file():
            raise ValueError(f"{scenario_id}: missing patch {patch}")
        patch_path = patch
    run_api, api_nodes, run_ui, ui_grep = _run(data.get("run"), scenario_id)
    whole_run = bool(data.get("whole_run", False))
    if stage == "install":
        if patch_path is not None or run_api or run_ui or services:
            raise ValueError(f"{scenario_id}: install scenarios only run the install step")
        if "dependency-install" not in tags:
            raise ValueError(f"{scenario_id}: install scenarios need dependency-install")
    expect = _expect(data.get("expect"), scenario_id, stage)
    if stage == "test" and whole_run and not run_api:
        raise ValueError(f"{scenario_id}: a whole-run outage runs the API suite")
    if stage == "test" and not whole_run:
        if not run_api and not run_ui:
            raise ValueError(f"{scenario_id}: scenario runs no tests")
        if run_api and not (expect.api_failed or expect.api_passed):
            raise ValueError(f"{scenario_id}: api run needs expectations")
        if run_ui and not (expect.ui_failed or expect.ui_passed):
            raise ValueError(f"{scenario_id}: ui run needs expectations")
        if not expect.api_failed and not expect.ui_failed:
            raise ValueError(f"{scenario_id}: scenario expects no failures")
    return Scenario(
        id=scenario_id,
        label=label,
        tags=tags,
        flow_id=_text(data, "flow_id"),
        stage=stage,
        summary=_text(data, "summary"),
        patch=patch_path,
        services=services,
        env=_env(data.get("env"), scenario_id),
        whole_run=whole_run,
        run_api=run_api,
        api_nodes=api_nodes,
        run_ui=run_ui,
        ui_grep=ui_grep,
        expect=expect,
        passes_with_env=_env(data.get("passes_with_env"), scenario_id),
    )


def _run(value: object, scenario_id: str) -> tuple[bool, tuple[str, ...], bool, str | None]:
    """Parse the run block into API and UI selectors."""
    data = _mapping(value, f"{scenario_id} run")
    if set(data) - {"api", "ui"}:
        raise ValueError(f"{scenario_id}: run only allows api and ui")
    run_api, api_nodes = _selector(data.get("api"), f"{scenario_id} run.api")
    ui = data.get("ui")
    if ui == "skip":
        return run_api, api_nodes, False, None
    if ui == "full":
        return run_api, api_nodes, True, None
    if isinstance(ui, str) and ui:
        return run_api, api_nodes, True, ui
    raise ValueError(f"{scenario_id}: run.ui must be full, skip, or a title")


def _selector(value: object, label: str) -> tuple[bool, tuple[str, ...]]:
    """Parse a full, skip, or node-id list selector."""
    if value == "full":
        return True, ()
    if value == "skip":
        return False, ()
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return True, tuple(value)
    raise ValueError(f"{label} must be full, skip, or a list of node ids")


def _expect(value: object, scenario_id: str, stage: str) -> Expectation:
    """Parse the expected pass and fail names."""
    if stage == "install":
        if value not in (None, {}):
            raise ValueError(f"{scenario_id}: install scenarios have no test expectations")
        return Expectation((), (), (), ())
    data = _mapping(value, f"{scenario_id} expect")
    allowed = {"api_failed", "api_passed", "ui_failed", "ui_passed"}
    if set(data) - allowed:
        raise ValueError(f"{scenario_id}: unknown expect fields")
    return Expectation(
        api_failed=_str_tuple(data.get("api_failed", []), f"{scenario_id} api_failed"),
        api_passed=_str_tuple(data.get("api_passed", []), f"{scenario_id} api_passed"),
        ui_failed=_str_tuple(data.get("ui_failed", []), f"{scenario_id} ui_failed"),
        ui_passed=_str_tuple(data.get("ui_passed", []), f"{scenario_id} ui_passed"),
    )


def _env(value: object, scenario_id: str) -> dict[str, str]:
    """Parse a string mapping, coercing integers from YAML."""
    if value is None:
        return {}
    data = _mapping(value, f"{scenario_id} env")
    parsed: dict[str, str] = {}
    for key, item in data.items():
        if not isinstance(key, str):
            raise ValueError(f"{scenario_id}: env keys must be strings")
        if isinstance(item, bool) or not isinstance(item, str | int):
            raise ValueError(f"{scenario_id}: env {key} must be a string")
        parsed[key] = str(item)
    return parsed


def _mapping(value: object, label: str) -> dict[str, Any]:
    """Return value when it is a mapping."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _text(data: dict[str, Any], key: str) -> str:
    """Return a required non-empty string field."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _str_tuple(value: object, label: str) -> tuple[str, ...]:
    """Return a tuple of non-empty strings."""
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return tuple(value)
