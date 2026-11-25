"""GitHub Actions client for run metadata, steps, logs, and artifacts."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Any

import httpx

from triage.schemas.bundle import PipelineStep, RunMetadata

API_ROOT = "https://api.github.com"


class GitHubActionsClient:
    """Read failed-run evidence from the GitHub Actions REST API."""

    def __init__(
        self,
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
        api_root: str = API_ROOT,
    ) -> None:
        """Use GITHUB_TOKEN / GH_TOKEN when no token is passed."""
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.api_root = api_root.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "failure-triage-collector",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=self.api_root, headers=headers, timeout=60.0)

    def close(self) -> None:
        """Close the underlying HTTP client when this instance created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubActionsClient:
        """Enter a context that closes the HTTP client on exit."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the HTTP client."""
        self.close()

    def get_run(self, owner: str, repo: str, run_id: int | str) -> dict[str, Any]:
        """Fetch one workflow run by id."""
        return self._get_json(f"/repos/{owner}/{repo}/actions/runs/{run_id}")

    def list_jobs(self, owner: str, repo: str, run_id: int | str) -> list[dict[str, Any]]:
        """List jobs for a workflow run."""
        payload = self._get_json(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
        jobs = payload.get("jobs")
        return jobs if isinstance(jobs, list) else []

    def list_artifacts(self, owner: str, repo: str, run_id: int | str) -> list[dict[str, Any]]:
        """List artifacts uploaded by a workflow run."""
        payload = self._get_json(f"/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts")
        artifacts = payload.get("artifacts")
        return artifacts if isinstance(artifacts, list) else []

    def download_artifact(
        self,
        owner: str,
        repo: str,
        artifact_id: int,
        dest_dir: Path,
    ) -> Path:
        """Download and unzip one artifact into dest_dir."""
        response = self._client.get(
            f"/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip",
            follow_redirects=True,
        )
        response.raise_for_status()
        dest_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            archive.extractall(dest_dir)
        return dest_dir

    def job_logs(self, owner: str, repo: str, job_id: int, *, max_chars: int = 200_000) -> str:
        """Download a job log and return a truncated text body."""
        response = self._client.get(
            f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
            follow_redirects=True,
        )
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        text = response.text
        if len(text) > max_chars:
            return text[:max_chars] + "\n...[truncated]...\n"
        return text

    def run_metadata(self, owner: str, repo: str, run_id: int | str) -> RunMetadata:
        """Build RunMetadata from the workflow run payload."""
        run = self.get_run(owner, repo, run_id)
        raw_commit = run.get("head_commit")
        head_commit: dict[str, Any] = raw_commit if isinstance(raw_commit, dict) else {}
        sha = run.get("head_sha") or head_commit.get("id")
        return RunMetadata(
            run_id=str(run.get("id") or run_id),
            workflow=str(run.get("name") or "") or None,
            workflow_id=int(run["workflow_id"]) if run.get("workflow_id") is not None else None,
            branch=str(run.get("head_branch") or "") or None,
            commit_sha=str(sha) if sha else None,
            event=str(run.get("event") or "") or None,
            attempt=int(run["run_attempt"]) if run.get("run_attempt") is not None else None,
            html_url=str(run.get("html_url") or "") or None,
            conclusion=str(run.get("conclusion") or "") or None,
            status=str(run.get("status") or "") or None,
            repository=f"{owner}/{repo}",
            source="github_actions",
        )

    def pipeline_steps(
        self,
        owner: str,
        repo: str,
        run_id: int | str,
        *,
        include_logs: bool = False,
    ) -> tuple[list[PipelineStep], list[int], dict[str, str]]:
        """Return step outcomes, job ids, and optional per-job log excerpts."""
        jobs = self.list_jobs(owner, repo, run_id)
        steps: list[PipelineStep] = []
        job_ids: list[int] = []
        logs: dict[str, str] = {}
        for job in jobs:
            job_name = str(job.get("name") or "job")
            job_id = job.get("id")
            if isinstance(job_id, int):
                job_ids.append(job_id)
                if include_logs:
                    logs[f"job:{job_name}"] = self.job_logs(owner, repo, job_id)
            for raw in job.get("steps") or []:
                if not isinstance(raw, dict):
                    continue
                steps.append(
                    PipelineStep(
                        job_name=job_name,
                        name=str(raw.get("name") or ""),
                        number=int(raw["number"]) if raw.get("number") is not None else None,
                        conclusion=str(raw.get("conclusion") or "") or None,
                        status=str(raw.get("status") or "") or None,
                        started_at=str(raw.get("started_at") or "") or None,
                        completed_at=str(raw.get("completed_at") or "") or None,
                    )
                )
        return steps, job_ids, logs

    def download_run_artifacts(
        self,
        owner: str,
        repo: str,
        run_id: int | str,
        dest_dir: Path,
    ) -> dict[str, Path]:
        """Download every non-expired artifact for a run into named subfolders."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        downloaded: dict[str, Path] = {}
        for artifact in self.list_artifacts(owner, repo, run_id):
            if artifact.get("expired"):
                continue
            name = str(artifact.get("name") or "artifact")
            artifact_id = artifact.get("id")
            if not isinstance(artifact_id, int):
                continue
            target = dest_dir / name
            self.download_artifact(owner, repo, artifact_id, target)
            downloaded[name] = target
        return downloaded

    def _get_json(self, path: str) -> dict[str, Any]:
        """GET a JSON object from the GitHub API."""
        response = self._client.get(path)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"expected object from {path}")
        return payload


def parse_repository(value: str) -> tuple[str, str]:
    """Split owner/repo into a tuple."""
    parts = value.strip().strip("/").split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("repository must look like owner/repo")
    return parts[0], parts[1]
