"""Typer CLI for the triage agent."""

from __future__ import annotations

from pathlib import Path

import typer

from triage import __version__
from triage.classify.pipeline import load_bundle, run_rules
from triage.collect.collector import collect_run
from triage.collect.local import collect_local
from triage.config import DEFAULT_CONFIG_PATH, load_config
from triage.smoke import format_report, smoke_bundles, smoke_execute

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


@app.command("run")
def run_saved_bundle(
    bundle: Path = typer.Option(
        ...,
        "--bundle",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Evidence bundle JSON to classify offline",
    ),
    out: Path = typer.Option(
        Path("verdicts.json"),
        "--out",
        help="Where to write the verdict JSON",
    ),
    trace: Path | None = typer.Option(
        None,
        "--trace",
        help="Where to write the run trace JSON (default: trace.json next to --out)",
    ),
    config: Path | None = typer.Option(None, "--config", help="Path to triage.config.yaml"),
) -> None:
    """Label a saved evidence bundle with the rules-only classifier."""
    cfg = load_config(config)
    report, run_trace = run_rules(load_bundle(bundle), cfg)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    trace_path = trace or out.with_name("trace.json")
    trace_path.write_text(run_trace.model_dump_json(indent=2) + "\n", encoding="utf-8")
    typer.echo(f"wrote {out}")
    typer.echo(f"wrote {trace_path}")
    typer.echo(report.examined)
    if not report.verdicts:
        typer.echo("no failures")
    for verdict in report.verdicts:
        typer.echo(
            f"{verdict.label} {verdict.confidence:.2f} {verdict.rule} "
            f"tests={len(verdict.test_ids)} flow={verdict.flow_id or '-'}"
        )


@app.command("smoke")
def smoke_command(
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Run each fault scenario, then classify it",
    ),
    bundles: Path | None = typer.Option(
        None,
        "--bundles",
        help="Directory of <scenario-id>/evidence_bundle.json from an earlier smoke run",
    ),
    manifest: Path = typer.Option(
        Path("faults/manifest.yaml"),
        "--manifest",
        help="Fault catalog used as the expected labels",
    ),
    out: Path = typer.Option(Path("bundles/smoke"), "--out", help="Directory for the smoke report"),
    scenario: list[str] | None = typer.Option(
        None,
        "--scenario",
        help="Limit to these scenario ids",
    ),
    config: Path | None = typer.Option(None, "--config", help="Path to triage.config.yaml"),
) -> None:
    """Classify the fault catalog and list verdicts that miss the manifest."""
    cfg = load_config(config)
    root = (config or DEFAULT_CONFIG_PATH).resolve().parent
    if not manifest.is_file():
        manifest = root / "faults" / "manifest.yaml"
    if execute:
        rows, code = smoke_execute(
            root=root,
            manifest=manifest,
            out_dir=out,
            config=cfg,
            only=scenario,
        )
    elif bundles is not None:
        rows, code = smoke_bundles(
            bundles_dir=bundles,
            manifest=manifest,
            out_dir=out,
            config=cfg,
            only=scenario,
        )
    else:
        raise typer.BadParameter("pass --execute or --bundles")
    typer.echo(format_report(rows))
    raise typer.Exit(code)


if __name__ == "__main__":
    app()
