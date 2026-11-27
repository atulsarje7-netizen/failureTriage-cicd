"""Render a bug-report draft as standalone markdown."""

from __future__ import annotations

from triage.schemas.report import BugReportDraft


def render_markdown(draft: BugReportDraft) -> str:
    """Render a self-sufficient markdown body that survives expired CI artifacts."""
    lines = [
        f"## {draft.title}",
        "",
        f"- **Label:** `{draft.label}`",
        f"- **Severity:** `{draft.severity}`",
        f"- **Confidence:** {draft.confidence:.2f}",
        f"- **Stage:** `{draft.stage}`",
        f"- **Cluster:** `{draft.cluster_id}`",
    ]
    if draft.tags:
        lines.append(f"- **Tags:** {', '.join(f'`{tag}`' for tag in draft.tags)}")
    if draft.run_id:
        lines.append(f"- **Run:** `{draft.run_id}`")
    if draft.branch:
        lines.append(f"- **Branch:** `{draft.branch}`")
    if draft.commit_sha:
        lines.append(f"- **Commit:** `{draft.commit_sha}`")
    if draft.environment:
        lines.append(f"- **Environment:** {draft.environment}")
    if draft.signature:
        lines.append(f"- **Signature:** `{draft.signature}`")
    lines.extend(["", "### Affected tests", ""])
    if draft.affected_tests:
        lines.extend(f"- `{test_id}`" for test_id in draft.affected_tests)
    else:
        lines.append("- _(none)_")
    lines.extend(["", "### Failure", ""])
    if draft.failing_step:
        lines.append(f"- **Failing step:** {draft.failing_step}")
    if draft.expected:
        lines.append(f"- **Expected:** {draft.expected}")
    if draft.actual:
        lines.append(f"- **Actual:** {draft.actual}")
    lines.extend(["", "### Evidence", ""])
    if draft.evidence:
        for cite in draft.evidence:
            lines.append(f"- `{cite.source}:{cite.ref}` — {cite.note}")
    else:
        lines.append("- _(none)_")
    lines.extend(["", "### Suspect change", ""])
    if draft.suspect_commit:
        lines.append(f"- **Commit:** `{draft.suspect_commit}`")
    if draft.suspect_files:
        lines.extend(f"- `{path}`" for path in draft.suspect_files)
    else:
        lines.append("- _(no product/test/config files cited)_")
    if draft.reproduction:
        lines.extend(["", "### Reproduction", "", "```bash", draft.reproduction, "```"])
    if draft.suggested_fix:
        lines.extend(
            [
                "",
                "### Suggested fix",
                "",
                "> Suggestion only — based on a cited diff hunk or log line.",
                "",
                draft.suggested_fix,
            ]
        )
    if draft.flakiness_score is not None or draft.quarantine_recommendation:
        lines.extend(["", "### Flakiness", ""])
        if draft.flakiness_score is not None:
            lines.append(f"- **Score:** {draft.flakiness_score:.2f}")
        if draft.quarantine_recommendation:
            lines.append(f"- **Recommendation:** {draft.quarantine_recommendation}")
        else:
            lines.append("- _(no quarantine recommended)_")
    lines.append("")
    return "\n".join(lines)
