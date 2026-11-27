"""Resolve the last green commit SHA for verification."""

from __future__ import annotations

from triage.collect.github import GitHubActionsClient, parse_repository
from triage.schemas.bundle import EvidenceBundle


def resolve_last_green_sha(
    bundle: EvidenceBundle,
    *,
    override: str | None = None,
    client: GitHubActionsClient | None = None,
) -> tuple[str | None, str | None]:
    """Return (sha, skip_reason) for the last successful run on the same branch."""
    if override:
        return override, None
    repository = bundle.run.repository
    branch = bundle.run.branch
    if not repository or not branch:
        return None, "missing repository or branch on the evidence bundle"
    owner, repo = parse_repository(repository)
    owns_client = client is None
    gh = client or GitHubActionsClient()
    try:
        sha = gh.find_last_green_sha(
            owner,
            repo,
            branch=branch,
            workflow_id=bundle.run.workflow_id,
            exclude_sha=bundle.run.commit_sha,
        )
    except Exception as exc:  # noqa: BLE001 - surface as skip reason for the verifier
        return None, f"last-green lookup failed: {type(exc).__name__}: {exc}"
    finally:
        if owns_client:
            gh.close()
    if not sha:
        return None, "no successful prior run found on this branch"
    return sha, None
