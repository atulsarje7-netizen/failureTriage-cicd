"""Assemble normalised reports into one EvidenceBundle."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from triage import __version__
from triage.collect.exchanges import load_exchange
from triage.collect.trace import extract_http_calls
from triage.schemas.bundle import (
    EvidenceBundle,
    ExaminedCount,
    FileChange,
    FlowOutcome,
    HttpCall,
    LayerOutcome,
    PipelineStep,
    RunMetadata,
    RunTotals,
    TestResult,
    TestStatus,
)

_EXECUTED: frozenset[TestStatus] = frozenset(
    {"passed", "failed", "flaky", "timed_out", "interrupted"}
)
_FAILED_STATUSES: frozenset[TestStatus] = frozenset({"failed", "timed_out", "interrupted"})


def attach_api_exchanges(tests: list[TestResult], exchange_dir: Path | None) -> None:
    """Attach captured HTTP exchanges onto failed API tests when files exist."""
    if exchange_dir is None or not exchange_dir.is_dir():
        return
    for test in tests:
        if test.layer != "api" or test.status not in _FAILED_STATUSES:
            continue
        call = load_exchange(exchange_dir, test.test_id)
        if call is not None:
            test.http_calls = [call]


def attach_ui_traces(tests: list[TestResult], results_dir: Path | None) -> None:
    """Attach HTTP calls from Playwright trace.zip files onto UI tests."""
    if results_dir is None or not results_dir.is_dir():
        return
    for test in tests:
        if test.layer != "ui":
            continue
        trace_path = _resolve_trace(test, results_dir)
        if trace_path is None:
            continue
        calls = extract_http_calls(trace_path)
        if calls:
            test.http_calls = calls
        if str(trace_path) not in test.attachments:
            test.attachments.append(str(trace_path))


def build_bundle(
    *,
    run: RunMetadata,
    tests: list[TestResult],
    steps: list[PipelineStep] | None = None,
    never_ran: list[TestResult] | None = None,
    artifact_paths: dict[str, str] | None = None,
    logs: dict[str, str] | None = None,
    notes: list[str] | None = None,
    changes: list[FileChange] | None = None,
) -> EvidenceBundle:
    """Build an evidence bundle with totals, examined count, and flow matrix."""
    never = list(never_ran or [])
    all_tests = list(tests)
    totals = compute_totals(all_tests, never)
    examined = ExaminedCount(
        examined=sum(1 for test in all_tests if test.status in _EXECUTED),
        total=totals.reported,
    )
    return EvidenceBundle(
        agent_version=__version__,
        run=run,
        steps=list(steps or []),
        tests=all_tests,
        never_ran=never,
        flows=build_flow_outcomes(all_tests),
        totals=totals,
        examined=examined,
        artifact_paths=dict(artifact_paths or {}),
        logs=dict(logs or {}),
        notes=list(notes or []),
        changes=list(changes or []),
    )


def compute_totals(tests: list[TestResult], never_ran: list[TestResult]) -> RunTotals:
    """Count statuses across reported tests and never-ran entries."""
    totals = RunTotals(never_ran=len(never_ran))
    for test in tests:
        if test.status == "passed":
            totals.passed += 1
        elif test.status == "failed":
            totals.failed += 1
        elif test.status == "skipped":
            totals.skipped += 1
        elif test.status == "flaky":
            totals.flaky += 1
        elif test.status == "timed_out":
            totals.timed_out += 1
        elif test.status == "interrupted":
            totals.interrupted += 1
        elif test.status == "never_ran":
            totals.never_ran += 1
    return totals


def build_flow_outcomes(tests: list[TestResult]) -> list[FlowOutcome]:
    """Summarise API and UI outcomes per flow_id for the layer matrix."""
    by_flow: dict[str, list[TestResult]] = defaultdict(list)
    for test in tests:
        if test.flow_id:
            by_flow[test.flow_id].append(test)
    outcomes: list[FlowOutcome] = []
    for flow_id in sorted(by_flow):
        group = by_flow[flow_id]
        api_tests = [t for t in group if t.layer == "api" and t.status != "skipped"]
        ui_tests = [t for t in group if t.layer == "ui" and t.status != "skipped"]
        ui_http: list[HttpCall] = []
        for test in ui_tests:
            ui_http.extend(test.http_calls)
        outcomes.append(
            FlowOutcome(
                flow_id=flow_id,
                api=_layer_outcome(api_tests),
                ui=_layer_outcome(ui_tests),
                ui_http_statuses=_unique_calls(ui_http),
                api_test_ids=[t.test_id for t in api_tests],
                ui_test_ids=[t.test_id for t in ui_tests],
            )
        )
    return outcomes


def save_bundle(bundle: EvidenceBundle, path: Path) -> Path:
    """Write the evidence bundle as indented JSON and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _layer_outcome(tests: list[TestResult]) -> LayerOutcome:
    """Collapse a layer's tests into passed, failed, mixed, or not_run."""
    if not tests:
        return "not_run"
    failed = any(t.status in _FAILED_STATUSES for t in tests)
    passed = any(t.status in {"passed", "flaky"} for t in tests)
    if failed and passed:
        return "mixed"
    if failed:
        return "failed"
    return "passed"


def _unique_calls(calls: list[HttpCall]) -> list[HttpCall]:
    """Deduplicate HTTP calls by method, URL, and status."""
    seen: set[tuple[str, str, int | None]] = set()
    unique: list[HttpCall] = []
    for call in calls:
        key = (call.method, call.url, call.status_code)
        if key in seen:
            continue
        seen.add(key)
        unique.append(call)
    return unique


def _resolve_trace(test: TestResult, results_dir: Path) -> Path | None:
    """Find a trace.zip for a UI test from attachments or the results directory."""
    for attachment in test.attachments:
        candidate = Path(attachment)
        if candidate.name == "trace.zip" and candidate.is_file():
            return candidate
        if candidate.suffix == ".zip" and "trace" in candidate.name and candidate.is_file():
            return candidate
    title = test.title or test.test_id
    slug = _slug(title)
    for path in results_dir.rglob("trace.zip"):
        if slug and slug in path.parent.name:
            return path
    return None


def _slug(value: str) -> str:
    """Approximate Playwright's output-directory slug for a test title."""
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")
