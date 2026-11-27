"""Unit tests for allow-listed verification and confidence scoring."""

from __future__ import annotations

import subprocess
import sys

import pytest

from triage.classify.confidence import (
    apply_confidence_and_guards,
    compute_confidence,
    has_flaky_rerun_evidence,
    rule_agreement,
)
from triage.classify.pipeline import run_rules
from triage.collect.assemble import build_bundle
from triage.collect.github import GitHubActionsClient
from triage.config import ConfidenceSettings, TriageConfig, VerifySettings, load_config
from triage.schemas.bundle import EvidenceBundle, FileChange, RunMetadata
from triage.schemas.bundle import TestResult as Result
from triage.schemas.verdict import RerunAttempt, Verdict, Verification
from triage.verify.allowlist import assert_allowed, bundle_test_ids
from triage.verify.commands import build_rerun_command
from triage.verify.green import resolve_last_green_sha
from triage.verify.runner import RunnerPaths, ScriptedTestRunner, SubprocessTestRunner
from triage.verify.verify import apply_verification


def _bundle(tests: list[Result], **run_fields: object) -> EvidenceBundle:
    """Build a minimal evidence bundle for verification tests."""
    meta = {
        "run_id": "verify-case",
        "source": "local",
        "commit_sha": "abc123",
        "branch": "main",
        "repository": "owner/repo",
        "workflow_id": 9,
    }
    meta.update(run_fields)
    return build_bundle(
        run=RunMetadata(**meta),  # type: ignore[arg-type]
        tests=tests,
        changes=[FileChange(path="app/shop.py", kind="product", added_lines=1, removed_lines=0)],
    )


def _api_fail() -> Result:
    """One failed API test used across verification cases."""
    return Result(
        test_id="tests/api/test_orders.py::test_checkout",
        layer="api",
        flow_id="checkout",
        status="failed",
        file="tests/api/test_orders.py",
        title="test_checkout",
        message="AssertionError: assert 1 == 1299",
    )


def test_allowlist_rejects_unknown_test_id() -> None:
    """The verifier refuses test ids that are not in the evidence bundle."""
    bundle = _bundle([_api_fail()])
    allowed = bundle_test_ids(bundle)
    assert "tests/api/test_orders.py::test_checkout" in allowed
    with pytest.raises(ValueError, match="not in evidence bundle"):
        assert_allowed("tests/api/test_orders.py::not_real", allowed)


def test_build_rerun_commands_for_api_and_ui() -> None:
    """API ids map to pytest nodeids and UI ids map to Playwright file + title."""
    from pathlib import Path

    api = build_rerun_command(
        _api_fail(),
        repo_root=Path("/repo"),
        ui_cwd=Path("/repo/tests/ui"),
    )
    assert api.argv == [
        sys.executable,
        "-m",
        "pytest",
        "tests/api/test_orders.py::test_checkout",
        "-q",
        "--tb=no",
    ]
    ui_test = Result(
        test_id="checkout.spec.ts > places an order",
        layer="ui",
        flow_id="checkout",
        status="failed",
        file="checkout.spec.ts",
        title="places an order",
    )
    ui = build_rerun_command(ui_test, repo_root=Path("/repo"), ui_cwd=Path("/repo/tests/ui"))
    assert ui.argv == ["npx", "playwright", "test", "checkout.spec.ts", "-g", "places an order"]
    assert ui.cwd == Path("/repo/tests/ui")


def test_scripted_runner_is_allowlisted() -> None:
    """A scripted runner still refuses ids outside the bundle."""
    bundle = _bundle([_api_fail()])
    runner = ScriptedTestRunner({"tests/api/test_orders.py::test_checkout": "passed"})
    ok = runner.run("tests/api/test_orders.py::test_checkout", sha="abc123", bundle=bundle)
    assert ok.outcome == "passed"
    with pytest.raises(ValueError, match="not in evidence bundle"):
        runner.run("tests/api/other.py::nope", sha="abc123", bundle=bundle)


def test_subprocess_runner_executes_requested_commit(tmp_path) -> None:
    """The runner checks out the requested commit instead of the live worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    source = repo / "tests/api/test_orders.py"
    source.parent.mkdir(parents=True)
    source.write_text("EXPECTED = 'committed'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    source.write_text("EXPECTED = 'working tree'\n", encoding="utf-8")
    bundle = _bundle([_api_fail()])

    def executor(argv: list[str], *, cwd, **kwargs) -> subprocess.CompletedProcess[str]:
        del argv, kwargs
        assert (cwd / "tests/api/test_orders.py").read_text(encoding="utf-8") == (
            "EXPECTED = 'committed'\n"
        )
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    runner = SubprocessTestRunner(
        RunnerPaths(repo_root=repo, ui_cwd=repo / "tests/ui"), executor=executor
    )
    attempt = runner.run(_api_fail().test_id, sha=sha, bundle=bundle)
    assert attempt.outcome == "passed"
    assert attempt.sha == sha


def test_subprocess_runner_rejects_missing_sha_without_execution(tmp_path) -> None:
    """A rerun without an immutable commit SHA never executes the worktree."""
    called = False

    def executor(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not execute without a full commit SHA")

    runner = SubprocessTestRunner(
        RunnerPaths(repo_root=tmp_path, ui_cwd=tmp_path / "tests/ui"), executor=executor
    )
    attempt = runner.run(_api_fail().test_id, sha="abc123", bundle=_bundle([_api_fail()]))
    assert attempt.outcome == "error"
    assert called is False


def test_verify_fills_same_sha_and_last_green_summaries() -> None:
    """Enabled verification records same-SHA and last-green attempts on the verdict."""
    bundle = _bundle([_api_fail()])
    report, _trace = run_rules(bundle, TriageConfig(abstain_below=0.5), verify=False)
    assert report.verdicts
    runner = ScriptedTestRunner({"tests/api/test_orders.py::test_checkout": "failed"})
    cfg = TriageConfig(
        abstain_below=0.5,
        verify=VerifySettings(
            enabled=True,
            same_sha_attempts=2,
            last_green=True,
            last_green_sha="def456",
        ),
    )
    verified = apply_verification(
        report,
        bundle,
        cfg,
        runner=runner,
        verify_enabled=True,
    )
    verdict = verified.verdicts[0]
    assert verdict.verification is not None
    assert verdict.verification.same_sha_reruns == "0 of 2 passed"
    assert len(verdict.verification.same_sha_attempts) == 2
    assert verdict.verification.last_green_commit == "def456"
    assert verdict.verification.last_green_sha == "0 of 1 passed"
    assert verdict.confidence_inputs is not None
    assert "regression" not in verdict.tags  # last green failed, so not a regression


def test_last_green_pass_tags_regression() -> None:
    """A pass on the last green SHA tags a persistent failure as a regression."""
    bundle = _bundle([_api_fail()])
    report, _ = run_rules(bundle, TriageConfig(abstain_below=0.5), verify=False)

    class SplitRunner:
        """Fail on the failing SHA and pass on the green SHA."""

        def run(self, test_id: str, *, sha: str | None, bundle: EvidenceBundle) -> RerunAttempt:
            outcome = "passed" if sha == "green" else "failed"
            return RerunAttempt(test_id=test_id, sha=sha, outcome=outcome)  # type: ignore[arg-type]

    cfg = TriageConfig(
        abstain_below=0.5,
        verify=VerifySettings(
            enabled=True,
            same_sha_attempts=1,
            last_green_sha="green",
        ),
    )
    verified = apply_verification(
        report,
        bundle,
        cfg,
        runner=SplitRunner(),  # type: ignore[arg-type]
        verify_enabled=True,
    )
    assert "regression" in verified.verdicts[0].tags
    assert verified.verdicts[0].confidence_inputs is not None
    assert verified.verdicts[0].confidence_inputs.last_green_passed is True


def test_mixed_same_sha_supports_flaky() -> None:
    """Mixed same-SHA outcomes are enough rerun evidence for flaky_test."""
    verification = Verification(
        same_sha_attempts=[
            RerunAttempt(test_id="t", sha="a", outcome="passed"),
            RerunAttempt(test_id="t", sha="a", outcome="failed"),
        ]
    )
    assert has_flaky_rerun_evidence(verification)
    verdict = Verdict(
        test_id="t",
        test_ids=["t"],
        cluster_id="c1",
        label="needs_human",
        confidence=0.4,
        runner_up={"label": "flaky_test", "confidence": 0.8},
        tags=["ui"],
        evidence=[{"source": "test", "ref": "t", "note": "mixed"}],
        verification=verification,
        needs_human=True,
        rule="llm:submit_verdicts",
    )
    scored = apply_confidence_and_guards(
        verdict,
        settings=ConfidenceSettings(),
        abstain_below=0.5,
    )
    assert scored.label == "flaky_test"
    assert scored.needs_human is False
    assert scored.confidence_inputs is not None


def test_flaky_without_rerun_evidence_abstains() -> None:
    """flaky_test without same-run or mixed same-SHA evidence becomes needs_human."""
    verdict = Verdict(
        test_id="t",
        test_ids=["t"],
        cluster_id="c1",
        label="flaky_test",
        confidence=0.9,
        tags=["ui"],
        evidence=[{"source": "test", "ref": "t", "note": "guess"}],
        verification=Verification(skipped_reason="verify_disabled"),
        rule="guess_flaky",
    )
    scored = apply_confidence_and_guards(
        verdict,
        settings=ConfidenceSettings(),
        abstain_below=0.5,
    )
    assert scored.label == "needs_human"
    assert "_flaky_without_rerun" in scored.rule


def test_every_rules_verdict_records_confidence_inputs() -> None:
    """The rules pipeline always attaches confidence inputs, even with verify off."""
    bundle = _bundle([_api_fail()])
    report, trace = run_rules(bundle, TriageConfig(), verify=False)
    assert report.verdicts
    for verdict in report.verdicts:
        assert verdict.confidence_inputs is not None
        assert 0.0 <= verdict.confidence <= 1.0
        assert verdict.verification is not None
        assert verdict.verification.skipped_reason == "verify_disabled"
    assert any(event.name == "compute_confidence" for event in trace.events)
    assert any(event.name == "verify_reruns" for event in trace.events)


def test_confidence_formula_uses_weights() -> None:
    """Composite confidence blends rule agreement and sample agreement when reruns are absent."""
    inputs = compute_confidence(
        rule_confidence=0.9,
        runner_up_confidence=0.1,
        verification=None,
        sample_agreement=1.0,
        label="product_bug",
        settings=ConfidenceSettings(weight_rule=0.5, weight_rerun=0.3, weight_sample=0.2),
    )
    assert inputs.rerun_support is None
    assert "rerun" not in inputs.weights
    expected_agreement = rule_agreement(0.9, 0.1)
    assert inputs.rule_agreement == pytest.approx(expected_agreement)
    assert inputs.raw_score == pytest.approx((0.5 * expected_agreement + 0.2 * 1.0) / 0.7)


def test_resolve_last_green_sha_from_github() -> None:
    """Last-green resolution reads successful workflow runs from GitHub."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert "workflows/9/runs" in str(request.url)
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {"head_sha": "abc123"},
                    {"head_sha": "green999"},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.github.com")
    gh = GitHubActionsClient(token="x", client=client)
    bundle = _bundle([_api_fail()])
    sha, reason = resolve_last_green_sha(bundle, client=gh)
    assert reason is None
    assert sha == "green999"
    override, override_reason = resolve_last_green_sha(bundle, override="manual", client=gh)
    assert override == "manual"
    assert override_reason is None


def test_load_config_reads_verify_and_confidence() -> None:
    """triage.config.yaml supplies verify and confidence settings."""
    config = load_config()
    assert config.verify.enabled is False
    assert config.verify.same_sha_attempts == 3
    assert config.confidence.weight_rule == 0.4
    assert config.confidence.llm_samples == 1
