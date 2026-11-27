"""Run allow-listed verification and attach results to every verdict."""

from __future__ import annotations

from pathlib import Path

from triage.classify.confidence import apply_confidence_and_guards
from triage.classify.recording import Tracer
from triage.collect.github import GitHubActionsClient
from triage.config import TriageConfig
from triage.schemas.bundle import EvidenceBundle
from triage.schemas.verdict import RerunAttempt, Verdict, VerdictReport, Verification
from triage.verify.allowlist import failed_ids_for_verdict
from triage.verify.green import resolve_last_green_sha
from triage.verify.runner import RunnerPaths, SubprocessTestRunner, TestRunner


def apply_verification(
    report: VerdictReport,
    bundle: EvidenceBundle,
    config: TriageConfig,
    *,
    tracer: Tracer | None = None,
    runner: TestRunner | None = None,
    green_client: GitHubActionsClient | None = None,
    verify_enabled: bool | None = None,
) -> VerdictReport:
    """Fill verification fields, score confidence, and enforce flaky/abstain guards."""
    active = tracer or Tracer()
    enabled = config.verify.enabled if verify_enabled is None else verify_enabled
    with active.step(
        "verify_reruns",
        {
            "enabled": enabled,
            "verdicts": len(report.verdicts),
            "same_sha_attempts": config.verify.same_sha_attempts,
        },
    ) as box:
        verified: list[Verdict] = []
        for verdict in report.verdicts:
            verified.append(
                _verify_one(
                    verdict,
                    bundle,
                    config,
                    enabled=enabled,
                    runner=runner,
                    green_client=green_client,
                )
            )
        box["output"] = {
            "verified": len(verified),
            "with_same_sha": sum(
                1 for item in verified if item.verification and item.verification.same_sha_attempts
            ),
        }

    with active.step("compute_confidence", {"verdicts": len(verified)}) as box:
        scored = [
            apply_confidence_and_guards(
                item,
                settings=config.confidence,
                abstain_below=config.abstain_below,
            )
            for item in verified
        ]
        box["output"] = {
            "scored": len(scored),
            "needs_human": sum(1 for item in scored if item.needs_human),
            "mean_confidence": (
                round(sum(item.confidence for item in scored) / len(scored), 4) if scored else 0.0
            ),
        }
    return report.model_copy(update={"verdicts": scored})


def verify_report(
    report: VerdictReport,
    bundle: EvidenceBundle,
    config: TriageConfig,
    *,
    tracer: Tracer | None = None,
    runner: TestRunner | None = None,
    green_client: GitHubActionsClient | None = None,
    verify_enabled: bool | None = None,
) -> VerdictReport:
    """Alias for apply_verification used by the package export."""
    return apply_verification(
        report,
        bundle,
        config,
        tracer=tracer,
        runner=runner,
        green_client=green_client,
        verify_enabled=verify_enabled,
    )


def _verify_one(
    verdict: Verdict,
    bundle: EvidenceBundle,
    config: TriageConfig,
    *,
    enabled: bool,
    runner: TestRunner | None,
    green_client: GitHubActionsClient | None,
) -> Verdict:
    """Attach verification to one verdict without changing its label yet."""
    existing = verdict.verification or Verification()
    same_run = existing.same_run_retries
    if not enabled:
        verification = Verification(
            same_run_retries=same_run,
            skipped_reason="verify_disabled",
        )
        return verdict.model_copy(update={"verification": verification})

    test_ids = failed_ids_for_verdict(verdict.test_ids or [verdict.test_id], bundle)
    if not test_ids:
        verification = Verification(
            same_run_retries=same_run,
            skipped_reason="no_allowlisted_failed_tests",
        )
        return verdict.model_copy(update={"verification": verification})

    active_runner = runner or _default_runner(config)
    same_sha = bundle.run.commit_sha
    same_attempts: list[RerunAttempt] = []
    for test_id in test_ids:
        for _ in range(config.verify.same_sha_attempts):
            same_attempts.append(active_runner.run(test_id, sha=same_sha, bundle=bundle))

    last_green_attempts: list[RerunAttempt] = []
    last_green_commit: str | None = None
    last_green_summary: str | None = None
    skip_bits: list[str] = []
    if config.verify.last_green:
        last_green_commit, green_skip = resolve_last_green_sha(
            bundle,
            override=config.verify.last_green_sha,
            client=green_client,
        )
        if green_skip:
            skip_bits.append(green_skip)
        elif last_green_commit:
            for test_id in test_ids:
                last_green_attempts.append(
                    active_runner.run(test_id, sha=last_green_commit, bundle=bundle)
                )
            last_green_summary = _summarize_attempts(last_green_attempts)
        else:
            skip_bits.append("last-green sha unavailable")
    else:
        skip_bits.append("last_green_disabled")

    verification = Verification(
        same_sha_reruns=_summarize_attempts(same_attempts),
        last_green_sha=last_green_summary,
        same_run_retries=same_run,
        last_green_commit=last_green_commit,
        same_sha_attempts=same_attempts,
        last_green_attempts=last_green_attempts,
        skipped_reason="; ".join(skip_bits) if skip_bits else None,
    )
    return verdict.model_copy(update={"verification": verification})


def _default_runner(config: TriageConfig) -> SubprocessTestRunner:
    """Build the subprocess runner from config paths."""
    root = Path(config.verify.repo_root).resolve()
    ui = root / config.verify.ui_cwd
    return SubprocessTestRunner(
        RunnerPaths(repo_root=root, ui_cwd=ui, test_roots=tuple(config.paths.test)),
        timeout_seconds=config.verify.timeout_seconds,
    )


def _summarize_attempts(attempts: list[RerunAttempt]) -> str | None:
    """Render an N-of-M passed summary for decisive attempts."""
    decisive = [item for item in attempts if item.outcome in {"passed", "failed"}]
    if not decisive:
        if not attempts:
            return None
        errors = sum(1 for item in attempts if item.outcome == "error")
        return f"0 of 0 passed ({errors} errors)"
    passed = sum(1 for item in decisive if item.outcome == "passed")
    return f"{passed} of {len(decisive)} passed"
