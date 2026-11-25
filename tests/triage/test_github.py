"""Unit tests for the GitHub Actions collector client."""

from __future__ import annotations

from pathlib import Path

import httpx

from triage.collect.github import GitHubActionsClient, parse_repository
from triage.schemas.bundle import PipelineStep


def test_parse_repository() -> None:
    """owner/repo splits into a pair."""
    assert parse_repository("acme/shop") == ("acme", "shop")


def test_run_metadata_and_steps_from_api() -> None:
    """Run metadata and step outcomes come from the Actions API, not log keywords."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/actions/runs/42"):
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "name": "Fault scenario",
                    "workflow_id": 9,
                    "head_branch": "main",
                    "head_sha": "abc123",
                    "event": "workflow_dispatch",
                    "run_attempt": 1,
                    "html_url": "https://github.com/acme/shop/actions/runs/42",
                    "conclusion": "failure",
                    "status": "completed",
                },
            )
        if request.url.path.endswith("/actions/runs/42/jobs"):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 7,
                            "name": "scenario",
                            "steps": [
                                {
                                    "name": "Install Python dependencies",
                                    "number": 3,
                                    "conclusion": "success",
                                    "status": "completed",
                                },
                                {
                                    "name": "Run scenario",
                                    "number": 8,
                                    "conclusion": "failure",
                                    "status": "completed",
                                },
                            ],
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request {request.url}")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.github.com")
    gh = GitHubActionsClient(token="unused", client=client)
    meta = gh.run_metadata("acme", "shop", 42)
    assert meta.run_id == "42"
    assert meta.workflow == "Fault scenario"
    assert meta.commit_sha == "abc123"
    assert meta.conclusion == "failure"
    assert meta.source == "github_actions"

    steps, job_ids, logs = gh.pipeline_steps("acme", "shop", 42, include_logs=False)
    assert job_ids == [7]
    assert logs == {}
    assert steps == [
        PipelineStep(
            job_name="scenario",
            name="Install Python dependencies",
            number=3,
            conclusion="success",
            status="completed",
        ),
        PipelineStep(
            job_name="scenario",
            name="Run scenario",
            number=8,
            conclusion="failure",
            status="completed",
        ),
    ]
    # Prove the failure stage is the scenario step without scanning log text.
    failed = [step for step in steps if step.conclusion == "failure"]
    assert [step.name for step in failed] == ["Run scenario"]
    client.close()


def test_download_artifact_unzips(tmp_path: Path) -> None:
    """Artifact zip responses are extracted into the destination directory."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("test-results/api/junit.xml", "<testsuites/>")
    payload = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/artifacts/99/zip")
        return httpx.Response(200, content=payload)

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.github.com")
    gh = GitHubActionsClient(token="unused", client=http)
    dest = tmp_path / "art"
    gh.download_artifact("acme", "shop", 99, dest)
    assert (dest / "test-results" / "api" / "junit.xml").is_file()
    http.close()
