"""Rule-based severity for draft bugs (never from the model)."""

from __future__ import annotations

from triage.schemas.bundle import EvidenceBundle
from triage.schemas.report import Severity
from triage.schemas.verdict import Verdict


def score_severity(verdict: Verdict, bundle: EvidenceBundle) -> Severity:
    """Score severity from affected tests, branch, and whether the failure blocks merge."""
    branch = (bundle.run.branch or "").lower()
    on_main = branch in {"main", "master"}
    affected = len(verdict.test_ids) or 1
    blocks_merge = on_main or verdict.stage in {"install", "lint", "build"} or affected >= 5
    if on_main and (affected >= 2 or verdict.stage != "test"):
        return "critical"
    if blocks_merge or affected >= 3:
        return "high"
    if verdict.label == "flaky_test":
        return "low"
    if affected >= 2:
        return "medium"
    return "medium"
