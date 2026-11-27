"""Bug report drafting and publishing."""

from triage.report.draft import draft_from_verdict, draft_reports, summary_markdown
from triage.report.issues import allow_issue_writes, require_issue_write_allowed
from triage.report.publish import PublishResult, publish_drafts
from triage.report.severity import score_severity

__all__ = [
    "PublishResult",
    "allow_issue_writes",
    "draft_from_verdict",
    "draft_reports",
    "publish_drafts",
    "require_issue_write_allowed",
    "score_severity",
    "summary_markdown",
]
