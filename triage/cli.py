"""Typer CLI for the triage agent."""

from __future__ import annotations

from pathlib import Path

import typer

from triage import __version__
from triage.audit import AuditLog
from triage.budget import BudgetGuard
from triage.classify.pipeline import load_bundle, run_classify
from triage.collect.collector import collect_run
from triage.collect.local import collect_local
from triage.config import DEFAULT_CONFIG_PATH, budget_settings_from_config, load_config
from triage.feedback import FeedbackStore, find_verdict
from triage.report.draft import draft_reports
from triage.report.publish import publish_drafts
from triage.schemas.audit import AuditAction
from triage.schemas.report import DraftReport
from triage.schemas.trace import TraceEvent
from triage.schemas.verdict import Label, RunnerUp, VerdictReport
from triage.smoke import format_report, smoke_bundles, smoke_execute
from triage.store.history import HistoryStore

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
    drafts_out: Path | None = typer.Option(
        None,
        "--drafts",
        help="Where to write bug-report drafts JSON (default: drafts.json next to --out)",
    ),
    trace: Path | None = typer.Option(
        None,
        "--trace",
        help="Where to write the run trace JSON (default: trace.json next to --out)",
    ),
    engine: str = typer.Option(
        "rules",
        "--engine",
        help="Classifier: rules (default) or llm (Gemini via local .env)",
    ),
    verify: bool | None = typer.Option(
        None,
        "--verify/--no-verify",
        help="Allow-listed same-SHA / last-green reruns (default: verify.enabled in config)",
    ),
    publish: bool = typer.Option(
        False,
        "--publish/--no-publish",
        help="Publish draft issues / PR comment (default: write drafts only)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="With --publish, render the summary without GitHub writes",
    ),
    pull: int | None = typer.Option(
        None,
        "--pull",
        help="Pull request number for the triage PR comment",
    ),
    config: Path | None = typer.Option(None, "--config", help="Path to triage.config.yaml"),
) -> None:
    """Label a saved evidence bundle with rules or the LLM tool loop."""
    cfg = load_config(config)
    evidence = load_bundle(bundle)
    history: HistoryStore | None = None
    if cfg.history.enabled:
        history = HistoryStore(cfg.history.path)
    try:
        report, run_trace = run_classify(
            evidence,
            cfg,
            engine=engine,
            verify=verify,
            history=history,
        )
        budget = BudgetGuard(budget_settings_from_config(cfg))
        budget_status = budget.sync_from_trace(run_trace)
        if not budget_status.ok:
            report = _abstain_for_budget(report, budget_status.exceeded or "unknown")
        if not budget_status.ok and engine == "rules":
            # Rules path has no mid-loop stop; record the breach for audit/trace consumers.
            run_trace.events.append(
                TraceEvent(
                    name="budget_exceeded",
                    kind="step",
                    inputs=budget_status.as_dict(),
                    result_size=0,
                    latency_ms=0.0,
                    error=f"budget_exceeded:{budget_status.exceeded}",
                )
            )

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        trace_path = trace or out.with_name("trace.json")
        trace_path.write_text(run_trace.model_dump_json(indent=2) + "\n", encoding="utf-8")

        if history is not None:
            recorded = history.record_bundle(evidence, report)
            typer.echo(f"history recorded={recorded} path={history.path}")

        draft_report = draft_reports(
            report,
            evidence,
            history=history,
            quarantine_at=cfg.history.quarantine_recommend_at,
        )
        drafts_path = drafts_out or out.with_name("drafts.json")
        drafts_path.write_text(draft_report.model_dump_json(indent=2) + "\n", encoding="utf-8")

        if cfg.audit.enabled:
            audit = AuditLog(cfg.audit.path)
            action: AuditAction = "budget_stop" if not budget_status.ok else "classify"
            audit.record_verdicts(
                report,
                action=action,
                budget=budget_status.as_dict(),
            )
            typer.echo(f"wrote audit {audit.path}")

        typer.echo(f"wrote {out}")
        typer.echo(f"wrote {trace_path}")
        typer.echo(f"wrote {drafts_path}")
        typer.echo(report.examined)
        if not budget_status.ok:
            typer.echo(f"budget exceeded: {budget_status.exceeded}")
        if not report.verdicts:
            typer.echo("no failures")
        for verdict in report.verdicts:
            typer.echo(
                f"{verdict.label} {verdict.confidence:.2f} {verdict.rule} "
                f"tests={len(verdict.test_ids)} flow={verdict.flow_id or '-'}"
            )
        typer.echo(f"drafts={len(draft_report.drafts)} skipped={len(draft_report.skipped)}")
        if publish:
            result = publish_drafts(
                draft_report,
                config=cfg,
                repository=cfg.repository or evidence.run.repository,
                pull_number=pull,
                dry_run=dry_run,
                summary_path=out.with_name("triage-summary.md"),
            )
            typer.echo(
                f"publish dry_run={result.dry_run} pr_comment={result.pr_comment_posted} "
                f"created={result.issues_created} updated={result.issues_updated}"
            )
            for note in result.notes:
                typer.echo(note)
    finally:
        if history is not None:
            history.close()


@app.command("publish")
def publish_command(
    drafts: Path = typer.Option(
        ...,
        "--drafts",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Drafts JSON from a prior triage run",
    ),
    pull: int | None = typer.Option(
        None,
        "--pull",
        help="Pull request number for the triage PR comment",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Render the summary without GitHub writes",
    ),
    repository: str | None = typer.Option(
        None,
        "--repo",
        help="owner/repo (defaults to config / draft repository)",
    ),
    summary: Path | None = typer.Option(
        None,
        "--summary",
        help="Where to write the triage summary markdown",
    ),
    config: Path | None = typer.Option(None, "--config", help="Path to triage.config.yaml"),
) -> None:
    """Publish previously drafted bugs without re-classifying."""
    cfg = load_config(config)
    draft_report = DraftReport.model_validate_json(drafts.read_text(encoding="utf-8"))
    repo = repository or cfg.repository
    if repo is None:
        for draft in draft_report.drafts:
            if draft.repository:
                repo = draft.repository
                break
    result = publish_drafts(
        draft_report,
        config=cfg,
        repository=repo,
        pull_number=pull,
        dry_run=dry_run,
        summary_path=summary or drafts.with_name("triage-summary.md"),
    )
    typer.echo(
        f"publish dry_run={result.dry_run} pr_comment={result.pr_comment_posted} "
        f"created={result.issues_created} updated={result.issues_updated} "
        f"audit_entries={result.audit_entries}"
    )
    for note in result.notes:
        typer.echo(note)


@app.command("feedback")
def feedback_command(
    verdicts: Path = typer.Option(
        ...,
        "--verdicts",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Verdict JSON from a prior triage run",
    ),
    label: str = typer.Option(..., "--label", help="Confirmed or corrected primary label"),
    cluster: str | None = typer.Option(None, "--cluster", help="Cluster id to correct"),
    test_id: str | None = typer.Option(None, "--test-id", help="Test id contained in the cluster"),
    confirm: bool = typer.Option(
        False,
        "--confirm/--correct",
        help="Confirm the predicted label (default: correct it)",
    ),
    reviewer: str | None = typer.Option(None, "--reviewer", help="Who provided the feedback"),
    note: str | None = typer.Option(None, "--note", help="Optional free-text note"),
    bundle: Path | None = typer.Option(None, "--bundle", help="Optional evidence bundle path"),
    config: Path | None = typer.Option(None, "--config", help="Path to triage.config.yaml"),
) -> None:
    """Record a human confirmation or correction for later Evals ingestion."""
    if cluster is None and test_id is None:
        raise typer.BadParameter("pass --cluster or --test-id")
    cfg = load_config(config)
    report = VerdictReport.model_validate_json(verdicts.read_text(encoding="utf-8"))
    verdict = find_verdict(report, cluster_id=cluster, test_id=test_id)
    if verdict is None:
        raise typer.BadParameter("no matching verdict for --cluster/--test-id")
    corrected = _as_feedback_label(label)
    store = FeedbackStore(cfg.feedback.path)
    record = store.record(
        action="confirm" if confirm else "correct",
        corrected_label=corrected,
        verdict=verdict,
        run_id=report.run_id,
        reviewer=reviewer,
        note=note,
        bundle_path=str(bundle) if bundle else None,
    )
    typer.echo(f"wrote feedback {record.feedback_id} to {store.path}")
    typer.echo(
        f"{record.action} predicted={record.predicted_label} "
        f"corrected={record.corrected_label} cluster={record.cluster_id}"
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
    engine: str = typer.Option(
        "rules",
        "--engine",
        help="Classifier for smoke: rules or llm",
    ),
    verify: bool | None = typer.Option(
        None,
        "--verify/--no-verify",
        help="Allow-listed same-SHA / last-green reruns (default: verify.enabled in config)",
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
            engine=engine,
            verify=verify,
        )
    elif bundles is not None:
        rows, code = smoke_bundles(
            bundles_dir=bundles,
            manifest=manifest,
            out_dir=out,
            config=cfg,
            only=scenario,
            engine=engine,
            verify=verify,
        )
    else:
        raise typer.BadParameter("pass --execute or --bundles")
    typer.echo(format_report(rows))
    raise typer.Exit(code)


def _as_feedback_label(value: str) -> Label:
    """Validate a feedback label string."""
    allowed = {
        "flaky_test",
        "product_bug",
        "test_bug",
        "environment_issue",
        "build_break",
        "needs_human",
    }
    if value not in allowed:
        raise typer.BadParameter(f"label must be one of {sorted(allowed)}")
    return value  # type: ignore[return-value]


def _abstain_for_budget(report: VerdictReport, reason: str) -> VerdictReport:
    """Prevent over-budget classifications from producing actionable verdicts."""
    verdicts = []
    for verdict in report.verdicts:
        runner_up = verdict.runner_up
        if verdict.label != "needs_human":
            runner_up = RunnerUp(label=verdict.label, confidence=verdict.confidence)
        verdicts.append(
            verdict.model_copy(
                update={
                    "label": "needs_human",
                    "needs_human": True,
                    "runner_up": runner_up,
                    "rule": f"{verdict.rule}_budget_{reason}",
                }
            )
        )
    return report.model_copy(update={"verdicts": verdicts})


if __name__ == "__main__":
    app()
