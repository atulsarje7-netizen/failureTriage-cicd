"""Turn a GitHub Actions run id into a saved evidence bundle."""

from __future__ import annotations

from pathlib import Path

from triage.collect.assemble import save_bundle
from triage.collect.github import GitHubActionsClient, parse_repository
from triage.collect.local import collect_from_artifact_tree
from triage.config import TriageConfig, load_config
from triage.schemas.bundle import EvidenceBundle


def collect_run(
    run_id: str | int,
    *,
    repository: str | None = None,
    config: TriageConfig | None = None,
    work_dir: Path | None = None,
    out_path: Path | None = None,
    include_job_logs: bool = True,
    token: str | None = None,
) -> EvidenceBundle:
    """Download a failed run's artifacts and save one evidence bundle JSON."""
    cfg = config or load_config()
    repo = repository or cfg.repository
    if not repo:
        raise ValueError("repository is required (owner/repo or ci.repository in config)")
    owner, name = parse_repository(repo)
    root = work_dir or Path("bundles") / f"run-{run_id}"
    artifact_dir = root / "artifacts"
    root.mkdir(parents=True, exist_ok=True)

    with GitHubActionsClient(token=token) as client:
        run = client.run_metadata(owner, name, run_id)
        steps, job_ids, logs = client.pipeline_steps(
            owner, name, run_id, include_logs=include_job_logs
        )
        run.job_ids = job_ids
        client.download_run_artifacts(owner, name, run_id, artifact_dir)

    bundle = collect_from_artifact_tree(
        artifact_dir,
        config=cfg,
        run=run,
        steps=steps,
        logs=logs,
    )
    target = out_path or (root / "evidence_bundle.json")
    save_bundle(bundle, target)
    return bundle
