"""Run classifiers, verification, and stamp verdicts with versions and a trace."""

from __future__ import annotations

from pathlib import Path

from triage import __version__
from triage.classify.recording import Tracer
from triage.classify.rules import RuleVerdict, classify_bundle
from triage.config import TriageConfig, load_config
from triage.schemas.bundle import EvidenceBundle
from triage.schemas.trace import RunTrace
from triage.schemas.verdict import (
    RULES_PROMPT_VERSION,
    EvidenceCitation,
    Label,
    PrimaryLabel,
    RunnerUp,
    Stage,
    Verdict,
    VerdictReport,
    Verification,
)
from triage.verify.verify import apply_verification


def run_rules(
    bundle: EvidenceBundle,
    config: TriageConfig | None = None,
    *,
    verify: bool | None = None,
) -> tuple[VerdictReport, RunTrace]:
    """Classify one bundle with rules, then attach verification and confidence."""
    cfg = config or load_config()
    tracer = Tracer()
    decisions = classify_bundle(bundle, cfg, tracer)
    verdicts = [_to_verdict(item, bundle) for item in decisions]
    report = VerdictReport(
        agent_version=__version__,
        run_id=bundle.run.run_id,
        examined=bundle.examined.as_phrase(),
        verdicts=verdicts,
    )
    report = apply_verification(report, bundle, cfg, tracer=tracer, verify_enabled=verify)
    return report, tracer.finish(__version__)


def run_classify(
    bundle: EvidenceBundle,
    config: TriageConfig | None = None,
    *,
    engine: str = "rules",
    verify: bool | None = None,
) -> tuple[VerdictReport, RunTrace]:
    """Classify with rules or LLM, then attach verification and confidence."""
    cfg = config or load_config()
    if engine == "rules":
        return run_rules(bundle, cfg, verify=verify)
    if engine == "llm":
        from triage.llm.classify import run_llm
        from triage.llm.factory import build_llm_client

        return run_llm(bundle, cfg, build_llm_client(cfg), verify=verify)
    raise ValueError(f"unknown engine: {engine}")


def load_bundle(path: str | Path) -> EvidenceBundle:
    """Read an evidence bundle from a JSON path."""
    return EvidenceBundle.model_validate_json(Path(path).read_text(encoding="utf-8"))


def citation_supported(cite: EvidenceCitation, bundle: EvidenceBundle) -> bool:
    """Return whether a citation points at something the bundle actually contains."""
    if cite.source == "test":
        ids = {test.test_id for test in bundle.tests}
        ids.update(test.test_id for test in bundle.never_ran)
        ids.update({"(run)", "(install)", "(lint)", "(build)"})
        return cite.ref in ids
    if cite.source == "diff":
        return any(change.path == cite.ref for change in bundle.changes)
    if cite.source == "flow":
        return any(flow.flow_id == cite.ref for flow in bundle.flows)
    if cite.source == "log":
        return cite.ref in bundle.logs
    if cite.source == "step":
        return any(step.name == cite.ref for step in bundle.steps)
    if cite.source == "http":
        return any(
            cite.ref == f"{call.method} {call.url}"
            for test in bundle.tests
            for call in test.http_calls
        )
    if cite.source == "trace":
        return any(
            cite.ref == test.test_id or cite.ref in test.attachments for test in bundle.tests
        )
    return False


def _to_verdict(item: RuleVerdict, bundle: EvidenceBundle) -> Verdict:
    """Convert a rule decision into a versioned verdict with checked citations."""
    runner = _runner_up(item)
    verification = None
    if item.same_run_retries:
        verification = Verification(same_run_retries=item.same_run_retries)
    return Verdict(
        test_id=item.test_id,
        test_ids=list(item.test_ids),
        cluster_id=item.cluster_id,
        label=_as_label(item.label),
        confidence=item.confidence,
        runner_up=runner,
        tags=list(item.tags),
        stage=_as_stage(item.stage),
        evidence=[cite for cite in item.evidence if citation_supported(cite, bundle)],
        verification=verification,
        needs_human=item.needs_human or item.label == "needs_human",
        versions={
            "agent": __version__,
            "prompt": RULES_PROMPT_VERSION,
            "model": "rules",
        },
        signature=item.signature,
        flow_id=item.flow_id,
        rule=item.rule,
    )


def _runner_up(item: RuleVerdict) -> RunnerUp | None:
    """Return the runner-up when it is a primary label with a score."""
    label = _as_primary(item.runner_up_label)
    if label is None or item.runner_up_confidence is None:
        return None
    return RunnerUp(label=label, confidence=item.runner_up_confidence)


def _as_label(value: str) -> Label:
    """Coerce a rule label into the verdict literal."""
    if value == "flaky_test":
        return "flaky_test"
    if value == "product_bug":
        return "product_bug"
    if value == "test_bug":
        return "test_bug"
    if value == "environment_issue":
        return "environment_issue"
    if value == "build_break":
        return "build_break"
    return "needs_human"


def _as_primary(value: str | None) -> PrimaryLabel | None:
    """Return a primary label, or None when the value is an abstain."""
    if value == "flaky_test":
        return "flaky_test"
    if value == "product_bug":
        return "product_bug"
    if value == "test_bug":
        return "test_bug"
    if value == "environment_issue":
        return "environment_issue"
    if value == "build_break":
        return "build_break"
    return None


def _as_stage(value: str) -> Stage:
    """Coerce a stage name into the verdict literal."""
    if value == "install":
        return "install"
    if value == "lint":
        return "lint"
    if value == "build":
        return "build"
    return "test"
