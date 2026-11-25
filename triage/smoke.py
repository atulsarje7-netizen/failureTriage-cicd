"""Compare rules verdicts with the fault-catalog manifest."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from triage.classify.pipeline import run_rules
from triage.collect.diff import parse_unified_diff
from triage.collect.local import collect_local
from triage.config import TriageConfig, load_config
from triage.schemas.bundle import EvidenceBundle

SmokeKind = Literal["match", "abstain", "mismatch", "no_verdict", "error"]


@dataclass(frozen=True)
class SmokeScenario:
    """The manifest fields the smoke check is allowed to read."""

    id: str
    label: str
    patch: str | None


@dataclass
class SmokeRow:
    """One scenario's expected label against the labels the rules produced."""

    scenario_id: str
    expected: str
    actual: list[str]
    kind: SmokeKind
    rules: list[str]
    note: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready row."""
        return {
            "scenario_id": self.scenario_id,
            "expected": self.expected,
            "actual": self.actual,
            "kind": self.kind,
            "rules": self.rules,
            "note": self.note,
        }


def load_smoke_scenarios(path: Path) -> list[SmokeScenario]:
    """Read scenario id, expected label, and patch path from the manifest."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest root must be a mapping")
    entries = raw.get("scenarios")
    if not isinstance(entries, list):
        raise ValueError("manifest scenarios must be a list")
    scenarios: list[SmokeScenario] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        scenario_id = entry.get("id")
        label = entry.get("label")
        if not isinstance(scenario_id, str) or not isinstance(label, str):
            continue
        patch = entry.get("patch")
        scenarios.append(
            SmokeScenario(
                id=scenario_id,
                label=label,
                patch=patch if isinstance(patch, str) else None,
            )
        )
    return scenarios


def judge_labels(expected: str, labels: list[str]) -> SmokeKind:
    """Compare expected and actual labels. Abstain is every verdict needing a human."""
    if not labels:
        return "no_verdict"
    if all(label == expected for label in labels):
        return "match"
    if all(label == "needs_human" for label in labels):
        return "abstain"
    return "mismatch"


def overlay_patch(
    bundle: EvidenceBundle,
    patch_text: str | None,
    config: TriageConfig,
) -> EvidenceBundle:
    """Replace bundle changes with the scenario patch so local edits are not the diff."""
    changes = parse_unified_diff(patch_text, config.paths) if patch_text else []
    return bundle.model_copy(update={"changes": changes})


def smoke_execute(
    *,
    root: Path,
    manifest: Path,
    out_dir: Path,
    config: TriageConfig | None = None,
    only: list[str] | None = None,
) -> tuple[list[SmokeRow], int]:
    """Run each catalog scenario, classify its artifacts, and compare labels."""
    cfg = config or load_config()
    scenarios = load_smoke_scenarios(manifest)
    if only:
        wanted = set(only)
        scenarios = [item for item in scenarios if item.id in wanted]
    rows: list[SmokeRow] = []
    for scenario in scenarios:
        row = _execute_one(root, scenario, out_dir, cfg)
        rows.append(row)
        actual = ",".join(row.actual) if row.actual else "-"
        print(
            f"{scenario.id}: {row.kind} expected={row.expected} actual={actual}",
            flush=True,
        )
    incomplete = [row for row in rows if row.kind in {"error", "no_verdict"}]
    _write_report(out_dir, rows)
    return rows, 1 if incomplete else 0


def smoke_bundles(
    *,
    bundles_dir: Path,
    manifest: Path,
    out_dir: Path,
    config: TriageConfig | None = None,
    only: list[str] | None = None,
) -> tuple[list[SmokeRow], int]:
    """Classify saved bundles named <scenario-id>/evidence_bundle.json."""
    cfg = config or load_config()
    scenarios = load_smoke_scenarios(manifest)
    if only:
        wanted = set(only)
        scenarios = [item for item in scenarios if item.id in wanted]
    rows: list[SmokeRow] = []
    for scenario in scenarios:
        path = bundles_dir / scenario.id / "evidence_bundle.json"
        if not path.is_file():
            rows.append(
                SmokeRow(scenario.id, scenario.label, [], "no_verdict", [], f"missing {path}")
            )
            continue
        bundle = EvidenceBundle.model_validate_json(path.read_text(encoding="utf-8"))
        rows.append(_classify_row(scenario, bundle, cfg, note="saved bundle"))
    incomplete = [row for row in rows if row.kind in {"error", "no_verdict"}]
    _write_report(out_dir, rows)
    return rows, 1 if incomplete else 0


def format_report(rows: list[SmokeRow]) -> str:
    """Render the smoke rows as a plain text table."""
    kinds = ("match", "abstain", "mismatch", "no_verdict", "error")
    counts = {kind: sum(1 for row in rows if row.kind == kind) for kind in kinds}
    lines = [
        (
            f"matched={counts['match']} abstained={counts['abstain']} "
            f"mismatched={counts['mismatch']} no_verdict={counts['no_verdict']} "
            f"errors={counts['error']}"
        ),
        "",
    ]
    for row in rows:
        if row.kind == "match":
            continue
        actual = ", ".join(row.actual) if row.actual else "-"
        rules = ", ".join(row.rules) if row.rules else "-"
        lines.append(
            f"{row.kind}\t{row.scenario_id}\texpected={row.expected}\tactual={actual}\trules={rules}"
        )
        if row.note:
            lines.append(f"  {row.note}")
    if len(lines) == 2:
        lines.append("every scenario matched")
    return "\n".join(lines) + "\n"


def _execute_one(
    root: Path,
    scenario: SmokeScenario,
    out_dir: Path,
    config: TriageConfig,
) -> SmokeRow:
    """Execute one scenario and classify the artifacts it left behind."""
    completed = subprocess.run(
        [sys.executable, "-m", "faults", "execute", scenario.id],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    note = (completed.stdout + completed.stderr).strip().splitlines()
    tail = note[-1] if note else ""
    try:
        bundle = collect_local(root=root, config=config, run_id=scenario.id)
        patch_text = _read_patch(root, scenario.patch)
        bundle = overlay_patch(bundle, patch_text, config)
        dest = out_dir / scenario.id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "evidence_bundle.json").write_text(
            bundle.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        return _classify_row(scenario, bundle, config, note=tail, save_dir=dest)
    except Exception as exc:
        return SmokeRow(
            scenario.id,
            scenario.label,
            [],
            "error",
            [],
            f"{type(exc).__name__}: {exc}",
        )


def _classify_row(
    scenario: SmokeScenario,
    bundle: EvidenceBundle,
    config: TriageConfig,
    *,
    note: str,
    save_dir: Path | None = None,
) -> SmokeRow:
    """Classify a bundle and judge it against the scenario label."""
    report, trace = run_rules(bundle, config)
    if save_dir is not None:
        verdict_path = save_dir / "verdicts.json"
        trace_path = save_dir / "trace.json"
        verdict_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        trace_path.write_text(trace.model_dump_json(indent=2) + "\n", encoding="utf-8")
    labels = [str(verdict.label) for verdict in report.verdicts]
    rules = [verdict.rule for verdict in report.verdicts]
    kind = judge_labels(scenario.label, labels)
    return SmokeRow(scenario.id, scenario.label, labels, kind, rules, note)


def _read_patch(root: Path, patch: str | None) -> str | None:
    """Read a scenario patch relative to faults/."""
    if not patch:
        return None
    path = root / "faults" / patch
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _write_report(out_dir: Path, rows: list[SmokeRow]) -> None:
    """Write the smoke summary as JSON and plain text."""
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "rows": [row.to_dict() for row in rows],
        "matched": sum(1 for row in rows if row.kind == "match"),
        "abstained": sum(1 for row in rows if row.kind == "abstain"),
        "mismatched": sum(1 for row in rows if row.kind == "mismatch"),
        "no_verdict": sum(1 for row in rows if row.kind == "no_verdict"),
        "errors": sum(1 for row in rows if row.kind == "error"),
    }
    (out_dir / "smoke.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / "smoke.txt").write_text(format_report(rows), encoding="utf-8")
