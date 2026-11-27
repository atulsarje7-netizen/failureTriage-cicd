"""Build one self-sufficient draft bug per failure cluster."""

from __future__ import annotations

import shlex

from triage import __version__
from triage.report.render import render_markdown
from triage.report.severity import score_severity
from triage.schemas.bundle import EvidenceBundle, TestResult
from triage.schemas.report import BugReportDraft, DraftReport, DraftSkipReason
from triage.schemas.verdict import Verdict, VerdictReport
from triage.signals.signature import failure_text


def draft_reports(report: VerdictReport, bundle: EvidenceBundle) -> DraftReport:
    """Turn each clustered verdict into a draft or an explicit skip."""
    drafts: list[BugReportDraft] = []
    skipped: list[BugReportDraft] = []
    for index, verdict in enumerate(report.verdicts, start=1):
        draft = draft_from_verdict(verdict, bundle, index=index)
        if draft.publishable:
            drafts.append(draft)
        else:
            skipped.append(draft)
    return DraftReport(
        agent_version=__version__,
        run_id=report.run_id or bundle.run.run_id,
        examined=report.examined,
        drafts=drafts,
        skipped=skipped,
    )


def draft_from_verdict(
    verdict: Verdict,
    bundle: EvidenceBundle,
    *,
    index: int,
) -> BugReportDraft:
    """Build one draft for a verdict, marking unpublishable labels as skipped."""
    skip = _skip_reason(verdict)
    tests = _affected_tests(verdict, bundle)
    failing_step = _failing_step(verdict, bundle, tests)
    expected, actual = _expected_actual(verdict, tests)
    suspect_files = _suspect_files(verdict, bundle)
    reproduction = _reproduction(verdict, tests)
    suggested = _suggested_fix(verdict)
    title = _title(verdict, tests)
    draft = BugReportDraft(
        draft_id=f"{verdict.cluster_id}-{index}",
        cluster_id=verdict.cluster_id,
        title=title,
        label=verdict.label,
        confidence=verdict.confidence,
        tags=list(verdict.tags),
        severity=score_severity(verdict, bundle),
        stage=verdict.stage,
        run_id=bundle.run.run_id,
        branch=bundle.run.branch,
        commit_sha=bundle.run.commit_sha,
        repository=bundle.run.repository,
        environment=_environment(bundle),
        signature=verdict.signature,
        affected_tests=list(verdict.test_ids) or [verdict.test_id],
        failing_step=failing_step,
        expected=expected,
        actual=actual,
        evidence=list(verdict.evidence),
        suspect_commit=bundle.run.commit_sha,
        suspect_files=suspect_files,
        reproduction=reproduction,
        suggested_fix=suggested,
        publishable=skip is None,
        skip_reason=skip,
    )
    draft.markdown = render_markdown(draft)
    return draft


def _skip_reason(verdict: Verdict) -> DraftSkipReason | None:
    """Return why a verdict must not become a draft issue, if any."""
    if verdict.label == "needs_human" or verdict.needs_human:
        return "needs_human"
    if verdict.label == "build_break":
        return "build_break"
    if verdict.label == "flaky_test":
        return "flaky_recommendation_only"
    return None


def _title(verdict: Verdict, tests: list[TestResult]) -> str:
    """Build a short title that names the label and a representative failure."""
    tip = verdict.test_id
    if tests and failure_text(tests[0]):
        tip = failure_text(tests[0]).splitlines()[0][:80]
    elif verdict.signature:
        tip = verdict.signature[:80]
    return f"[{verdict.label}] {tip}"


def _environment(bundle: EvidenceBundle) -> str:
    """Summarise runner and workflow identity for the draft body."""
    parts: list[str] = []
    if bundle.run.workflow:
        parts.append(f"workflow={bundle.run.workflow}")
    if bundle.run.runner_labels:
        parts.append("runners=" + ",".join(bundle.run.runner_labels))
    if bundle.run.source:
        parts.append(f"source={bundle.run.source}")
    return "; ".join(parts) if parts else "unknown"


def _affected_tests(verdict: Verdict, bundle: EvidenceBundle) -> list[TestResult]:
    """Resolve verdict test ids back to bundle tests when present."""
    wanted = set(verdict.test_ids) | {verdict.test_id}
    return [test for test in bundle.tests if test.test_id in wanted]


def _failing_step(
    verdict: Verdict,
    bundle: EvidenceBundle,
    tests: list[TestResult],
) -> str | None:
    """Name the failing pipeline step or the first failed test step."""
    for cite in verdict.evidence:
        if cite.source == "step":
            return cite.ref
    for step in bundle.steps:
        if step.conclusion in {"failure", "cancelled"}:
            return step.name
    if tests:
        return tests[0].title or tests[0].test_id
    return None


def _expected_actual(
    verdict: Verdict,
    tests: list[TestResult],
) -> tuple[str | None, str | None]:
    """Derive expected vs actual from the stage and first failure message."""
    if verdict.stage == "install":
        return "dependencies install successfully", _first_message(tests, verdict)
    if verdict.stage == "lint":
        return "lint passes", _first_message(tests, verdict)
    if verdict.stage == "build":
        return "build succeeds", _first_message(tests, verdict)
    if not tests:
        return "tests pass", _first_message(tests, verdict)
    message = failure_text(tests[0]) or tests[0].message
    return "test passes", (message.splitlines()[0] if message else None)


def _first_message(tests: list[TestResult], verdict: Verdict) -> str | None:
    """Return the first usable failure line from tests or evidence notes."""
    if tests:
        text = failure_text(tests[0]) or tests[0].message
        if text:
            return text.splitlines()[0][:240]
    for cite in verdict.evidence:
        if cite.note:
            return cite.note.splitlines()[0][:240]
    return None


def _suspect_files(verdict: Verdict, bundle: EvidenceBundle) -> list[str]:
    """List cited or changed files that likely explain the failure."""
    cited = [cite.ref for cite in verdict.evidence if cite.source == "diff"]
    if cited:
        return cited
    return [change.path for change in bundle.changes[:8]]


def _reproduction(verdict: Verdict, tests: list[TestResult]) -> str | None:
    """Return a concrete command that reproduces the failure cluster."""
    if verdict.stage == "install":
        return "python -m pip install -e ."
    if verdict.stage == "lint":
        return "ruff check app triage faults tests/api"
    if verdict.stage == "build":
        return "docker compose build"
    if not tests and not verdict.test_ids:
        return None
    test_id = tests[0].test_id if tests else verdict.test_id
    if test_id.startswith("("):
        return None
    if tests and tests[0].layer == "ui":
        title = tests[0].title or test_id
        command = shlex.join(["npx", "playwright", "test", "-g", title])
        return f"cd tests/ui && {command}"
    return shlex.join(["pytest", test_id, "-q"])


def _suggested_fix(verdict: Verdict) -> str | None:
    """Include a suggested fix only when a citation grounds it."""
    diff_cites = [cite for cite in verdict.evidence if cite.source == "diff" and cite.note]
    if not diff_cites:
        return None
    cite = diff_cites[0]
    return f"Inspect `{cite.ref}` ({cite.note}). Marked as a suggestion only."


def summary_markdown(draft_report: DraftReport) -> str:
    """Render a short job-summary / PR comment covering drafts and skips."""
    lines = [
        "## Triage summary",
        "",
        f"Examined: {draft_report.examined}",
        f"Drafts: {len(draft_report.drafts)}",
        f"Skipped: {len(draft_report.skipped)}",
        "",
    ]
    if draft_report.drafts:
        lines.append("### Draft bugs")
        lines.append("")
        for draft in draft_report.drafts:
            lines.append(
                f"- **{draft.severity}** `{draft.label}` — {draft.title} "
                f"(confidence {draft.confidence:.2f})"
            )
        lines.append("")
    if draft_report.skipped:
        lines.append("### Skipped")
        lines.append("")
        for draft in draft_report.skipped:
            reason = draft.skip_reason or "skipped"
            lines.append(f"- `{draft.label}` ({reason}) — {draft.title}")
        lines.append("")
    return "\n".join(lines)


