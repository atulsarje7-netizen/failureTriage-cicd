"""Typer CLI for the triage agent."""

from __future__ import annotations

from pathlib import Path

import typer

from triage import __version__
from triage.collect.collector import collect_run
from triage.collect.local import collect_local
from triage.config import load_config

app = typer.Typer(add_completion=False, no_args_is_help=True, help="CI failure triage agent.")
collect_app = typer.Typer(add_completion=False, help="Build evidence bundles from CI runs.")
app.add_typer(collect_app, name="collect")


@app.callback()
def main() -> None:
    """Root command group for the triage CLI."""


@app.command("version")
def version() -> None:
    """Print the triage package version."""
    typer.echo(__version__)


@collect_app.command("run")
def collect_github_run(
    run_id: str = typer.Argument(..., help="GitHub Actions workflow run id"),
    repository: str | None = typer.Option(
        None,
        "--repo",
        help="owner/repo (defaults to ci.repository in triage.config.yaml)",
    ),
    out: Path = typer.Option(
        Path("bundles"),
        "--out",
        help="Directory for the downloaded artifacts and evidence_bundle.json",
    ),
    config: Path | None = typer.Option(None, "--config", help="Path to triage.config.yaml"),
    no_job_logs: bool = typer.Option(False, "--no-job-logs", help="Skip downloading job logs"),
) -> None:
    """Download a failed GitHub Actions run and save an evidence bundle."""
    cfg = load_config(config)
    work_dir = out / f"run-{run_id}"
    bundle = collect_run(
        run_id,
        repository=repository,
        config=cfg,
        work_dir=work_dir,
        out_path=work_dir / "evidence_bundle.json",
        include_job_logs=not no_job_logs,
    )
    typer.echo(f"wrote {work_dir / 'evidence_bundle.json'}")
    typer.echo(bundle.examined.as_phrase())
    typer.echo(
        f"totals passed={bundle.totals.passed} failed={bundle.totals.failed} "
        f"skipped={bundle.totals.skipped} flaky={bundle.totals.flaky} "
        f"never_ran={bundle.totals.never_ran}"
    )


@collect_app.command("local")
def collect_local_command(
    root: Path = typer.Option(Path("."), "--root", help="Repo root containing test-results/"),
    out: Path = typer.Option(
        Path("bundles/local-evidence_bundle.json"),
        "--out",
        help="Path for the saved evidence bundle JSON",
    ),
    run_id: str | None = typer.Option(None, "--run-id", help="Override the bundle run id"),
    config: Path | None = typer.Option(None, "--config", help="Path to triage.config.yaml"),
) -> None:
    """Build an evidence bundle from local JUnit and Playwright artifacts."""
    cfg = load_config(config)
    bundle = collect_local(root=root, config=cfg, run_id=run_id, out_path=out)
    typer.echo(f"wrote {out}")
    typer.echo(bundle.examined.as_phrase())
    typer.echo(
        f"totals passed={bundle.totals.passed} failed={bundle.totals.failed} "
        f"skipped={bundle.totals.skipped} flaky={bundle.totals.flaky} "
        f"never_ran={bundle.totals.never_ran}"
    )
    for flow in bundle.flows:
        typer.echo(f"flow {flow.flow_id}: api={flow.api} ui={flow.ui}")


if __name__ == "__main__":
    app()
