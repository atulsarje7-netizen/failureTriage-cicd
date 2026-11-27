"""Publish draft bugs as a job summary, PR comment, and draft GitHub issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from triage.config import TriageConfig, load_config
from triage.report.draft import summary_markdown
from triage.report.issues import GitHubIssueClient, IssueWriter, allow_issue_writes
from triage.schemas.report import BugReportDraft, DraftReport


@dataclass
class PublishResult:
    """What the publish step wrote (or would write in dry-run)."""

    summary_markdown: str
    pr_comment_posted: bool = False
    issues_created: list[int] = field(default_factory=list)
    issues_updated: list[int] = field(default_factory=list)
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)


def publish_drafts(
    draft_report: DraftReport,
    *,
    config: TriageConfig | None = None,
    repository: str | None = None,
    pull_number: int | None = None,
    writer: IssueWriter | None = None,
    dry_run: bool = False,
    summary_path: Path | None = None,
) -> PublishResult:
    """Write the job summary, optional PR comment, and draft issues under the write guard."""
    cfg = config or load_config()
    settings = cfg.publish
    summary = summary_markdown(draft_report)
    for draft in draft_report.drafts:
        summary += "\n---\n\n" + draft.markdown
    result = PublishResult(summary_markdown=summary, dry_run=dry_run)
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary + "\n", encoding="utf-8")
        result.notes.append(f"wrote summary {summary_path}")
    _append_github_step_summary(summary)

    if dry_run:
        result.notes.append("dry-run: skipped GitHub writes")
        return result

    owner, repo = _split_repository(repository or cfg.repository)
    if owner is None or repo is None:
        result.notes.append("no repository configured; skipped GitHub publishes")
        return result

    client = writer or GitHubIssueClient()
    try:
        if settings.pr_comment and pull_number is not None:
            client.create_or_update_comment(
                owner,
                repo,
                pull_number=pull_number,
                body=summary,
            )
            result.pr_comment_posted = True
        elif settings.pr_comment and pull_number is None:
            result.notes.append("pr_comment enabled but no pull_number provided")

        if settings.draft_issue and draft_report.drafts:
            with allow_issue_writes():
                for draft in draft_report.drafts:
                    number, updated = _upsert_draft_issue(
                        client,
                        owner,
                        repo,
                        draft,
                        label=settings.draft_issue_label,
                    )
                    if number is None:
                        continue
                    if updated:
                        result.issues_updated.append(number)
                    else:
                        result.issues_created.append(number)
    finally:
        close = getattr(client, "close", None)
        if callable(close) and writer is None:
            close()
    return result


def _upsert_draft_issue(
    writer: IssueWriter,
    owner: str,
    repo: str,
    draft: BugReportDraft,
    *,
    label: str,
) -> tuple[int | None, bool]:
    """Create or update a triage:draft issue, linking repeats by signature when possible."""
    labels = [label, draft.label]
    for tag in draft.tags[:4]:
        if tag not in labels:
            labels.append(tag)
    existing: dict[str, Any] | None = None
    finder = getattr(writer, "find_open_issue_by_signature", None)
    if callable(finder) and draft.signature:
        found = finder(owner, repo, draft.signature, label=label)
        existing = found if isinstance(found, dict) else None
    if existing and isinstance(existing.get("number"), int):
        writer.update_issue(
            owner,
            repo,
            int(existing["number"]),
            title=draft.title[:240],
            body=draft.markdown,
            labels=labels,
        )
        return int(existing["number"]), True
    created = writer.create_issue(
        owner,
        repo,
        title=draft.title[:240],
        body=draft.markdown,
        labels=labels,
    )
    number = created.get("number")
    if isinstance(number, int):
        return number, False
    return None, False


def _split_repository(value: str | None) -> tuple[str | None, str | None]:
    """Split owner/repo into parts."""
    if not value or "/" not in value:
        return None, None
    owner, repo = value.split("/", 1)
    if not owner or not repo:
        return None, None
    return owner, repo


def _append_github_step_summary(markdown: str) -> None:
    """Append the triage summary to GITHUB_STEP_SUMMARY when running in Actions."""
    import os

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(markdown)
        handle.write("\n")
