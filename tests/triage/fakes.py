"""Reusable fakes for triage package tests."""

from __future__ import annotations

from typing import Any

from triage.report.issues import require_issue_write_allowed


class FakeWriter:
    """In-memory GitHub writer for publish tests."""

    def __init__(self) -> None:
        """Start with empty create/update/comment logs."""
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self._next = 101

    def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Record a created issue."""
        require_issue_write_allowed()
        number = self._next
        self._next += 1
        payload = {
            "number": number,
            "title": title,
            "body": body,
            "labels": labels,
            "owner": owner,
            "repo": repo,
        }
        self.created.append(payload)
        return payload

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
        """Record an updated issue."""
        require_issue_write_allowed()
        payload = {
            "number": issue_number,
            "title": title,
            "body": body,
            "labels": labels,
            "state": state,
            "owner": owner,
            "repo": repo,
        }
        self.updated.append(payload)
        return payload

    def create_or_update_comment(
        self,
        owner: str,
        repo: str,
        *,
        issue_number: int | None = None,
        pull_number: int | None = None,
        body: str,
    ) -> dict[str, Any]:
        """Record a PR comment."""
        payload = {
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "issue_number": issue_number,
            "body": body,
        }
        self.comments.append(payload)
        return payload
