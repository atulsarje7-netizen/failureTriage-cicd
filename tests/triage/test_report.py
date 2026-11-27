"""Tests for bug-report drafts, severity rules, and the issue-write guard."""

from __future__ import annotations

import shlex
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from tests.triage.fakes import FakeWriter
from triage.classify.pipeline import run_rules
from triage.cli import app
from triage.collect.assemble import build_bundle
from triage.config import PublishSettings, TriageConfig
from triage.report.draft import draft_reports
from triage.report.issues import (
    GitHubIssueClient,
    allow_issue_writes,
    issue_writes_allowed,
    require_issue_write_allowed,
)
from triage.report.publish import publish_drafts
from triage.report.severity import score_severity
from triage.schemas.bundle import (
    EvidenceBundle,
    FileChange,
    PipelineStep,
    RunMetadata,
)
from triage.schemas.bundle import TestResult as EvidenceTestResult
from triage.schemas.verdict import EvidenceCitation, Verdict, VerdictReport


def _bundle(
    tests: list[EvidenceTestResult],
    *,
    changes: list[FileChange] | None = None,
    logs: dict[str, str] | None = None,
    steps: list[PipelineStep] | None = None,
    branch: str | None = "feature/x",
    commit: str | None = "abc123",
) -> EvidenceBundle:
    """Build a minimal evidence bundle for draft tests."""
    return build_bundle(
        run=RunMetadata(
            run_id="run-1",
            source="local",
            branch=branch,
            commit_sha=commit,
            repository="owner/repo",
            workflow="CI",
        ),
        tests=tests,
        changes=changes or [],
        logs=logs or {},
        steps=steps or [],
    )


def _failed(test_id: str = "tests/api/test_orders.py::test_checkout") -> EvidenceTestResult:
    """One failed API test."""
    return EvidenceTestResult(
        test_id=test_id,
        layer="api",
        flow_id="checkout",
        status="failed",
        file="tests/api/test_orders.py",
        message="AssertionError: expected 200",
        stack='File "tests/api/test_orders.py", line 10, in test_checkout\nAssertionError',
    )


def test_issue_writes_refused_outside_publish() -> None:
    """Creating issues is refused unless the publish guard is open."""
    assert issue_writes_allowed() is False
    with pytest.raises(PermissionError, match="publish step"):
        require_issue_write_allowed()
    with allow_issue_writes():
        assert issue_writes_allowed() is True
        require_issue_write_allowed()
    assert issue_writes_allowed() is False


def test_github_issue_client_guards_create_and_update() -> None:
    """The real GitHub adapter blocks mutations unless publishing is active."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"number": 12})

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="https://api.example.test"
    ) as http_client:
        writer = GitHubIssueClient(client=http_client)
        with pytest.raises(PermissionError, match="outside the publish step"):
            writer.create_issue("owner", "repo", title="draft", body="body", labels=[])
        with pytest.raises(PermissionError, match="outside the publish step"):
            writer.update_issue("owner", "repo", 12, title="draft")

        with allow_issue_writes():
            writer.create_issue("owner", "repo", title="draft", body="body", labels=[])
            writer.update_issue("owner", "repo", 12, title="draft")

    assert [request.method for request in requests] == ["POST", "PATCH"]


def test_ui_reproduction_quotes_untrusted_test_title() -> None:
    """A test title cannot add shell commands to the copied reproduction command."""
    title = 'checkout "slow"; echo injected'
    test = EvidenceTestResult(
        test_id="checkout.spec.ts > checkout",
        layer="ui",
        status="failed",
        file="checkout.spec.ts",
        title=title,
    )
    bundle = _bundle([test])
    verdict = Verdict(
        test_id=test.test_id,
        test_ids=[test.test_id],
        cluster_id="checkout",
        label="product_bug",
        confidence=0.9,
        rule="test",
    )
    draft = draft_reports(
        VerdictReport(agent_version="test", examined="examined 1 of 1", verdicts=[verdict]),
        bundle,
    ).drafts[0]
    assert draft.reproduction is not None
    assert shlex.split(draft.reproduction) == [
        "cd",
        "tests/ui",
        "&&",
        "npx",
        "playwright",
        "test",
        "-g",
        title,
    ]


def test_draft_is_self_sufficient_for_product_bug() -> None:
    """One cluster becomes a standalone draft with run id, tests, and reproduction."""
    bundle = _bundle(
        [_failed()],
        changes=[FileChange(path="app/shop.py", kind="product", added_lines=1)],
    )
    report, _trace = run_rules(bundle, TriageConfig())
    drafts = draft_reports(report, bundle)
    assert len(drafts.drafts) == 1
    draft = drafts.drafts[0]
    assert draft.publishable
    assert draft.run_id == "run-1"
    assert draft.affected_tests
    assert draft.reproduction
    assert "run-1" in draft.markdown
    assert draft.severity in {"critical", "high", "medium", "low"}
    assert draft.label == "product_bug"


def test_build_break_and_flaky_are_skipped_for_issues() -> None:
    """build_break and flaky_test do not become draft issues."""
    build_bundle_case = _bundle(
        [],
        changes=[FileChange(path="app/shop.py", kind="product", added_lines=1)],
        logs={"lint": "app/shop.py:1:1: F401 unused_lint_marker_app"},
        steps=[PipelineStep(job_name="local", name="Lint", conclusion="failure")],
    )
    report, _ = run_rules(build_bundle_case, TriageConfig())
    drafts = draft_reports(report, build_bundle_case)
    assert drafts.drafts == []
    assert drafts.skipped
    assert drafts.skipped[0].skip_reason == "build_break"
    assert drafts.skipped[0].label == "build_break"

    flaky = Verdict(
        test_id="tests/api/test_orders.py::test_checkout",
        test_ids=["tests/api/test_orders.py::test_checkout"],
        cluster_id="f1",
        label="flaky_test",
        confidence=0.9,
        tags=["api"],
        evidence=[
            EvidenceCitation(
                source="test",
                ref="tests/api/test_orders.py::test_checkout",
                note="recovered",
            )
        ],
        rule="same_run_recovery",
        signature="flake",
    )
    skipped = draft_reports(
        VerdictReport(
            agent_version=report.agent_version,
            run_id=report.run_id,
            examined=report.examined,
            verdicts=[flaky],
        ),
        build_bundle_case,
    ).skipped
    assert skipped[0].skip_reason == "flaky_recommendation_only"


def test_severity_is_critical_on_main() -> None:
    """Failures on main score as critical when more than one test is hit."""
    bundle = _bundle([_failed("a"), _failed("b")], branch="main")
    verdict = Verdict(
        test_id="a",
        test_ids=["a", "b"],
        cluster_id="c1",
        label="product_bug",
        confidence=0.9,
        rule="test",
    )
    assert score_severity(verdict, bundle) == "critical"


def test_publish_creates_draft_issue_under_guard() -> None:
    """Publish opens the write guard, posts a PR comment, and creates a draft issue."""
    bundle = _bundle(
        [_failed()],
        changes=[FileChange(path="app/shop.py", kind="product", added_lines=1)],
    )
    report, _ = run_rules(bundle, TriageConfig())
    drafts = draft_reports(report, bundle)
    writer = FakeWriter()
    result = publish_drafts(
        drafts,
        config=TriageConfig(
            repository="owner/repo",
            publish=PublishSettings(pr_comment=True, draft_issue=True),
        ),
        pull_number=7,
        writer=writer,
    )
    assert result.pr_comment_posted
    assert result.issues_created == [101]
    assert writer.comments and "Triage summary" in writer.comments[0]["body"]
    assert "triage:draft" in writer.created[0]["labels"]
    assert issue_writes_allowed() is False


def test_cli_writes_drafts_json(tmp_path) -> None:
    """triage run writes drafts.json next to the verdict file."""
    bundle = _bundle(
        [_failed()],
        changes=[FileChange(path="app/shop.py", kind="product", added_lines=1)],
    )
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json())
    out = tmp_path / "verdicts.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["run", "--bundle", str(bundle_path), "--out", str(out), "--no-verify"],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert out.with_name("drafts.json").is_file()
    assert "drafts=" in result.output


def test_cli_publish_uses_repository_from_bundle(tmp_path, monkeypatch) -> None:
    """The publish path has the repository even when config omits it."""
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(_bundle([_failed()]).model_dump_json())
    captured: dict[str, Any] = {}

    def fake_publish(draft_report, **kwargs):
        del draft_report
        captured.update(kwargs)
        return SimpleNamespace(
            dry_run=True,
            pr_comment_posted=False,
            issues_created=[],
            issues_updated=[],
            notes=["dry-run"],
        )

    monkeypatch.setattr("triage.cli.publish_drafts", fake_publish)
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--bundle",
            str(bundle_path),
            "--out",
            str(tmp_path / "verdicts.json"),
            "--publish",
            "--dry-run",
            "--no-verify",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["repository"] == "owner/repo"
