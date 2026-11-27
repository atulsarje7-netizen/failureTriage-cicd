"""Guarded GitHub issue create/update used only by the publish step."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import httpx

API_ROOT = "https://api.github.com"

_ALLOW_ISSUE_WRITES = False


class IssueWriter(Protocol):
    """Minimal surface for creating or updating a draft triage issue."""

    def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Create a GitHub issue and return the API payload."""

    def update_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        labels: list[str] | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing GitHub issue and return the API payload."""

    def create_or_update_comment(
        self,
        owner: str,
        repo: str,
        *,
        issue_number: int | None = None,
        pull_number: int | None = None,
        body: str,
    ) -> dict[str, Any]:
        """Post a PR or issue comment."""


@contextmanager
def allow_issue_writes() -> Iterator[None]:
    """Enable issue create/update for the duration of the publish step only."""
    global _ALLOW_ISSUE_WRITES
    previous = _ALLOW_ISSUE_WRITES
    _ALLOW_ISSUE_WRITES = True
    try:
        yield
    finally:
        _ALLOW_ISSUE_WRITES = previous


def issue_writes_allowed() -> bool:
    """Return whether the publish guard currently allows issue writes."""
    return _ALLOW_ISSUE_WRITES


def require_issue_write_allowed() -> None:
    """Refuse issue mutations outside the publish step."""
    if not _ALLOW_ISSUE_WRITES:
        raise PermissionError(
            "Creating or updating issues is refused outside the publish step"
        )


class GitHubIssueClient:
    """GitHub REST client that creates and updates issues under the write guard."""

    def __init__(
        self,
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
        api_root: str = API_ROOT,
    ) -> None:
        """Use GITHUB_TOKEN / GH_TOKEN when no token is passed."""
        import os

        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.api_root = api_root.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "failure-triage-publisher",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=self.api_root, headers=headers, timeout=60.0)

    def close(self) -> None:
        """Close the underlying HTTP client when this instance created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubIssueClient:
        """Enter a context that closes the HTTP client on exit."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the HTTP client."""
        self.close()

    def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Create a draft issue after the publish guard allows writes."""
        require_issue_write_allowed()
        response = self._client.post(
            f"/repos/{owner}/{repo}/issues",
            json={"title": title, "body": body, "labels": labels},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def update_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        labels: list[str] | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Update an issue after the publish guard allows writes."""
        require_issue_write_allowed()
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if labels is not None:
            payload["labels"] = labels
        if state is not None:
            payload["state"] = state
        response = self._client.patch(
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def find_open_issue_by_signature(
        self,
        owner: str,
        repo: str,
        signature: str,
        *,
        label: str,
    ) -> dict[str, Any] | None:
        """Find an open draft issue whose body still carries this failure signature."""
        query = f'repo:{owner}/{repo} is:issue is:open label:"{label}" "{signature}"'
        response = self._client.get("/search/issues", params={"q": query, "per_page": 5})
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            return None
        first = items[0]
        return first if isinstance(first, dict) else None

    def create_or_update_comment(
        self,
        owner: str,
        repo: str,
        *,
        issue_number: int | None = None,
        pull_number: int | None = None,
        body: str,
    ) -> dict[str, Any]:
        """Post a PR or issue comment (comments are not gated by the issue-write guard)."""
        number = pull_number if pull_number is not None else issue_number
        if number is None:
            raise ValueError("pull_number or issue_number is required")
        response = self._client.post(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json={"body": body},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
